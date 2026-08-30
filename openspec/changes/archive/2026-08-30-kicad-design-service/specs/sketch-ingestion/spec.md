# Sketch Ingestion Specification

## Purpose

Ingest electronic design sketches (images) and extract components and nets to feed the KiCad design pipeline.

## Requirements

### Requirement: Image Ingestion

The ingestion stage MUST accept a design sketch image and MUST validate that it contains recognizable electronic components and nets.

#### Scenario: Accept a valid sketch

- GIVEN a sketch image with recognizable components
- WHEN ingestion runs
- THEN it yields a component/net extraction

#### Scenario: Reject an empty sketch

- GIVEN a sketch image with no recognizable components
- WHEN ingestion runs
- THEN it reports the image as empty and does not feed the pipeline

### Requirement: Component and Net Extraction

The ingestion stage MUST extract components and nets and MUST record extraction confidence.

#### Scenario: Extract components and nets

- GIVEN a valid sketch
- WHEN extraction runs
- THEN it emits components and nets with a confidence score

#### Scenario: Low-confidence extraction

- GIVEN a sketch with ambiguous components
- WHEN extraction runs
- THEN it flags low confidence and does not auto-commit the plan

### Requirement: Pipeline Handoff

The ingestion stage MUST hand extracted components and nets to the KiCad design pipeline.

#### Scenario: Hand off to the pipeline

- GIVEN a validated extraction
- WHEN ingestion completes
- THEN the components and nets are handed to the KiCad design pipeline
