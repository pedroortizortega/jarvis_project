# Approved Knowledge Retrieval Specification

## Purpose

Return trustworthy, locally derived answers from published knowledge only.

## Requirements

### Requirement: Local hybrid published-note retrieval

The system MUST retrieve only published canonical notes using local lexical and semantic signals. It MUST retain only a current embeddings index that is reconstructible from published notes.

#### Scenario: Retrieve published knowledge

- GIVEN published notes and a local index
- WHEN a consumer searches for relevant knowledge
- THEN the system returns locally ranked notes

#### Scenario: Exclude unapproved content

- GIVEN pending or rejected proposals exist
- WHEN a consumer searches
- THEN no result contains their content

### Requirement: Stable citations and failure safety

The system MUST return each retrieval result with its note path and stable fragment citation. If the current index is unavailable or inconsistent, it MUST report retrieval unavailable rather than return uncited or stale results.

#### Scenario: Cite a result

- GIVEN a matching published note
- WHEN retrieval returns it
- THEN the result includes its note path and stable fragment

#### Scenario: Index outage

- GIVEN the current index cannot be safely queried
- WHEN retrieval is requested
- THEN the system returns an unavailable result without uncited knowledge
