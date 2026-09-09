"""Reviewable company entities and temporal relationships."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record
from app.clock import utcnow
from app.db.database import get_db
from app.db.models import (
    Document,
    DocumentChunk,
    Entity,
    EntityEvidence,
    EntityMention,
    ExtractionProposal,
    KnowledgeJob,
    Relationship,
    RelationshipEvidence,
)
from app.knowledge_schemas import (
    EntityCreate,
    EntityResponse,
    EntityUpdate,
    KnowledgeJobResponse,
    ProposalResponse,
    ProposalReview,
    RelationshipCreate,
    RelationshipResponse,
    RelationshipUpdate,
)
from app.security import Principal, require_human_member, require_organization_access
from app.services.document_service import get_document
from app.services.knowledge_service import (
    enqueue_knowledge,
    evidence_for_chunks,
    normalize_name,
)


router = APIRouter(prefix="/organizations/{organization_id}/knowledge", tags=["Knowledge"],
                   dependencies=[Depends(require_organization_access)])


def entity_response(db, organization_id, entity):
    chunk_ids = list(db.scalars(select(EntityEvidence.chunk_id).where(
        EntityEvidence.entity_id == entity.id)))
    return EntityResponse.model_validate(entity).model_copy(
        update={"evidence": evidence_for_chunks(db, organization_id, chunk_ids)})


def require_evidence(db, organization_id, chunk_ids):
    document_ids = list(db.scalars(select(DocumentChunk.document_id).where(
        DocumentChunk.id.in_(chunk_ids)).distinct()))
    documents = list(db.scalars(select(Document).where(
        Document.id.in_(document_ids), Document.organization_id == organization_id
    ).order_by(Document.id).with_for_update()))
    if len(documents) != len(document_ids) or any(item.status != "READY" for item in documents):
        raise HTTPException(409, "Evidence documents must be READY")
    evidence = evidence_for_chunks(db, organization_id, chunk_ids)
    if len(evidence) != len(set(chunk_ids)):
        raise HTTPException(400, "Every evidence chunk must belong to this organization")
    return evidence


def require_entity(db, organization_id, entity_id):
    entity = db.scalar(select(Entity).where(Entity.id == entity_id,
        Entity.organization_id == organization_id))
    if entity is None:
        raise HTTPException(404, "Entity not found")
    return entity


def create_entity_record(db, organization_id, payload, identity, confidence=1.0):
    require_evidence(db, organization_id, payload.evidence_chunk_ids)
    entity = Entity(organization_id=organization_id, entity_type=payload.entity_type,
        name=payload.name, normalized_name=normalize_name(payload.name),
        description=payload.description, valid_from=payload.valid_from, valid_to=payload.valid_to,
        confidence=confidence, created_by_user_id=identity.user_id)
    db.add(entity)
    db.flush()
    for chunk_id in payload.evidence_chunk_ids:
        db.add(EntityEvidence(entity_id=entity.id, chunk_id=chunk_id))
    return entity


def relationship_response(db, organization_id, relationship):
    subject = require_entity(db, organization_id, relationship.subject_id)
    object_ = require_entity(db, organization_id, relationship.object_id)
    chunk_ids = list(db.scalars(select(RelationshipEvidence.chunk_id).where(
        RelationshipEvidence.relationship_id == relationship.id)))
    return RelationshipResponse(id=relationship.id,
        subject=entity_response(db, organization_id, subject), predicate=relationship.predicate,
        object=entity_response(db, organization_id, object_), description=relationship.description,
        confidence=relationship.confidence, status=relationship.status,
        valid_from=relationship.valid_from, valid_to=relationship.valid_to,
        recorded_at=relationship.recorded_at,
        evidence=evidence_for_chunks(db, organization_id, chunk_ids))


def create_relationship_record(db, organization_id, payload, identity, confidence=1.0):
    subject = require_entity(db, organization_id, payload.subject_id)
    object_ = require_entity(db, organization_id, payload.object_id)
    if subject.status != "CONFIRMED" or object_.status != "CONFIRMED":
        raise HTTPException(409, "Relationships require confirmed entities")
    require_evidence(db, organization_id, payload.evidence_chunk_ids)
    relationship = Relationship(organization_id=organization_id,
        subject_id=payload.subject_id, predicate=payload.predicate, object_id=payload.object_id,
        description=payload.description, valid_from=payload.valid_from, valid_to=payload.valid_to,
        confidence=confidence, created_by_user_id=identity.user_id)
    db.add(relationship)
    db.flush()
    for chunk_id in payload.evidence_chunk_ids:
        db.add(RelationshipEvidence(relationship_id=relationship.id, chunk_id=chunk_id))
    return relationship


@router.get("/jobs", response_model=list[KnowledgeJobResponse])
def list_jobs(organization_id: UUID, db: Session = Depends(get_db),
              limit: int = Query(50, ge=1, le=200)):
    return list(db.scalars(select(KnowledgeJob).where(
        KnowledgeJob.organization_id == organization_id).order_by(
            KnowledgeJob.available_at.desc()).limit(limit)))


@router.post("/documents/{document_id}/extract", response_model=KnowledgeJobResponse,
             dependencies=[Depends(require_human_member)])
def retry_extraction(organization_id: UUID, document_id: UUID, db: Session = Depends(get_db)):
    document = get_document(db, organization_id, document_id, lock=True)
    if document.status != "READY":
        raise HTTPException(409, "Only READY documents can be extracted")
    job = enqueue_knowledge(db, document)
    record(db, "knowledge.extraction_queued", organization_id, document.id)
    db.commit()
    db.refresh(job)
    return job


@router.get("/proposals", response_model=list[ProposalResponse])
def proposals(organization_id: UUID, db: Session = Depends(get_db),
              status: str = Query("PROPOSED", pattern="^(PROPOSED|ACCEPTED|REJECTED)$"),
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return list(db.scalars(select(ExtractionProposal).where(
        ExtractionProposal.organization_id == organization_id,
        ExtractionProposal.status == status).order_by(
            ExtractionProposal.created_at, ExtractionProposal.id).limit(limit).offset(offset)))


@router.patch("/proposals/{proposal_id}", response_model=ProposalResponse,
              dependencies=[Depends(require_human_member)])
def review_proposal(organization_id: UUID, proposal_id: UUID, payload: ProposalReview,
                    identity: Principal = Depends(require_human_member),
                    db: Session = Depends(get_db)):
    # Use the same document-before-job/proposal lock order as both workers.
    candidate = db.scalar(select(ExtractionProposal).where(
        ExtractionProposal.id == proposal_id,
        ExtractionProposal.organization_id == organization_id))
    job = db.get(KnowledgeJob, candidate.knowledge_job_id) if candidate else None
    document = db.scalar(select(Document).where(
        Document.id == job.document_id,
        Document.organization_id == organization_id).with_for_update()) if job else None
    proposal = db.scalar(select(ExtractionProposal).where(
        ExtractionProposal.id == proposal_id,
        ExtractionProposal.organization_id == organization_id).with_for_update())
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    if document is None or document.status != "READY" or job.generation != document.index_generation:
        raise HTTPException(409, "Proposal belongs to an inactive source generation")
    if proposal.status != "PROPOSED":
        raise HTTPException(409, "Proposal was already reviewed")
    if payload.decision == "reject":
        proposal.status = "REJECTED"
        if proposal.kind == "entity":
            for mention in db.scalars(select(EntityMention).where(
                EntityMention.knowledge_job_id == proposal.knowledge_job_id,
                EntityMention.chunk_id == proposal.chunk_id,
                EntityMention.proposed_name == proposal.payload["name"],
                EntityMention.status == "PROPOSED")):
                mention.status = "REJECTED"
    elif proposal.kind == "entity":
        if payload.subject_id or payload.object_id:
            raise HTTPException(400, "Entity review accepts only entity_id")
        entity = require_entity(db, organization_id, payload.entity_id) if payload.entity_id else None
        if entity is not None and entity.entity_type != proposal.payload["type"]:
            raise HTTPException(409, "Mention type does not match the selected entity")
        if entity is None:
            create = EntityCreate(entity_type=proposal.payload["type"],
                name=proposal.payload["name"], description=proposal.payload.get("description"),
                valid_from=proposal.payload.get("valid_from"), valid_to=proposal.payload.get("valid_to"),
                evidence_chunk_ids=[proposal.chunk_id])
            entity = create_entity_record(db, organization_id, create, identity, proposal.confidence)
        elif db.get(EntityEvidence, (entity.id, proposal.chunk_id)) is None:
            db.add(EntityEvidence(entity_id=entity.id, chunk_id=proposal.chunk_id))
        entity.status = "CONFIRMED"
        proposal.resolved_entity_id = entity.id
        for mention in db.scalars(select(EntityMention).where(
            EntityMention.knowledge_job_id == proposal.knowledge_job_id,
            EntityMention.chunk_id == proposal.chunk_id,
            EntityMention.proposed_name == proposal.payload["name"],
            EntityMention.status == "PROPOSED")):
            mention.entity_id, mention.status = entity.id, "LINKED"
    else:
        if not payload.subject_id or not payload.object_id or payload.entity_id:
            raise HTTPException(400, "Relationship review requires subject_id and object_id")
        create = RelationshipCreate(subject_id=payload.subject_id,
            predicate=proposal.payload["predicate"], object_id=payload.object_id,
            description=proposal.payload.get("description"),
            valid_from=proposal.payload.get("valid_from"), valid_to=proposal.payload.get("valid_to"),
            evidence_chunk_ids=[proposal.chunk_id])
        relationship = create_relationship_record(db, organization_id, create, identity,
                                                  proposal.confidence)
        proposal.resolved_relationship_id = relationship.id
    proposal.status = "ACCEPTED"
    proposal.reviewed_by_user_id = identity.user_id
    proposal.reviewed_at = utcnow()
    record(db, "knowledge.proposal_" + proposal.status.lower(), organization_id, proposal.id,
           kind=proposal.kind)
    db.commit()
    db.refresh(proposal)
    return proposal


@router.post("/entities", response_model=EntityResponse, status_code=201,
             dependencies=[Depends(require_human_member)])
def create_entity(organization_id: UUID, payload: EntityCreate,
                  identity: Principal = Depends(require_human_member), db: Session = Depends(get_db)):
    entity = create_entity_record(db, organization_id, payload, identity)
    record(db, "knowledge.entity_created", organization_id, entity.id, entity_type=entity.entity_type)
    db.commit()
    return entity_response(db, organization_id, entity)


@router.get("/entities", response_model=list[EntityResponse])
def list_entities(organization_id: UUID, db: Session = Depends(get_db),
                  entity_type: str | None = Query(None, pattern="^(person|project|decision)$"),
                  include_archived: bool = False, limit: int = Query(100, ge=1, le=200)):
    statement = select(Entity).where(Entity.organization_id == organization_id)
    if entity_type:
        statement = statement.where(Entity.entity_type == entity_type)
    if not include_archived:
        statement = statement.where(Entity.status == "CONFIRMED")
    entities = db.scalars(statement.order_by(Entity.name, Entity.id).limit(limit))
    return [entity_response(db, organization_id, entity) for entity in entities]


@router.get("/entities/{entity_id}", response_model=EntityResponse)
def get_entity(organization_id: UUID, entity_id: UUID, db: Session = Depends(get_db)):
    return entity_response(db, organization_id, require_entity(db, organization_id, entity_id))


@router.patch("/entities/{entity_id}", response_model=EntityResponse,
              dependencies=[Depends(require_human_member)])
def update_entity(organization_id: UUID, entity_id: UUID, payload: EntityUpdate,
                  db: Session = Depends(get_db)):
    entity = require_entity(db, organization_id, entity_id)
    values = payload.model_dump(exclude_unset=True)
    valid_from = values.get("valid_from", entity.valid_from)
    valid_to = values.get("valid_to", entity.valid_to)
    if valid_from and valid_to and valid_to <= valid_from:
        raise HTTPException(422, "valid_to must be after valid_from")
    for key, value in values.items():
        setattr(entity, key, value)
    if payload.name is not None:
        entity.name = " ".join(payload.name.split())
        entity.normalized_name = normalize_name(entity.name)
    record(db, "knowledge.entity_updated", organization_id, entity.id,
           fields=sorted(values))
    db.commit()
    return entity_response(db, organization_id, entity)


@router.post("/relationships", response_model=RelationshipResponse, status_code=201,
             dependencies=[Depends(require_human_member)])
def create_relationship(organization_id: UUID, payload: RelationshipCreate,
                        identity: Principal = Depends(require_human_member),
                        db: Session = Depends(get_db)):
    relationship = create_relationship_record(db, organization_id, payload, identity)
    record(db, "knowledge.relationship_created", organization_id, relationship.id,
           predicate=relationship.predicate)
    db.commit()
    return relationship_response(db, organization_id, relationship)


@router.get("/relationships", response_model=list[RelationshipResponse])
def list_relationships(organization_id: UUID, db: Session = Depends(get_db),
                       include_inactive: bool = False, as_of: datetime | None = None,
                       limit: int = Query(100, ge=1, le=200)):
    statement = select(Relationship).where(Relationship.organization_id == organization_id)
    if not include_inactive:
        moment = as_of or utcnow()
        if moment.tzinfo is not None:
            moment = moment.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(Relationship.status == "CONFIRMED",
            (Relationship.valid_from.is_(None) | (Relationship.valid_from <= moment)),
            (Relationship.valid_to.is_(None) | (Relationship.valid_to > moment)))
    relationships = db.scalars(statement.order_by(Relationship.recorded_at.desc()).limit(limit))
    return [relationship_response(db, organization_id, item) for item in relationships]


@router.get("/relationships/{relationship_id}", response_model=RelationshipResponse)
def get_relationship(organization_id: UUID, relationship_id: UUID,
                     db: Session = Depends(get_db)):
    relationship = db.scalar(select(Relationship).where(Relationship.id == relationship_id,
        Relationship.organization_id == organization_id))
    if relationship is None:
        raise HTTPException(404, "Relationship not found")
    return relationship_response(db, organization_id, relationship)


@router.patch("/relationships/{relationship_id}", response_model=RelationshipResponse,
              dependencies=[Depends(require_human_member)])
def update_relationship(organization_id: UUID, relationship_id: UUID,
                        payload: RelationshipUpdate, db: Session = Depends(get_db)):
    relationship = db.scalar(select(Relationship).where(Relationship.id == relationship_id,
        Relationship.organization_id == organization_id).with_for_update())
    if relationship is None:
        raise HTTPException(404, "Relationship not found")
    values = payload.model_dump(exclude_unset=True)
    valid_from = values.get("valid_from", relationship.valid_from)
    valid_to = values.get("valid_to", relationship.valid_to)
    if valid_from and valid_to and valid_to <= valid_from:
        raise HTTPException(422, "valid_to must be after valid_from")
    for key, value in values.items():
        setattr(relationship, key, value)
    record(db, "knowledge.relationship_updated", organization_id, relationship.id,
           fields=sorted(values))
    db.commit()
    return relationship_response(db, organization_id, relationship)
