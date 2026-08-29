---
name: deep-research
description: Multi-phase research using web tools and fallback methods.
tags: [research, deep-dive, technical, analysis, multi-phase]
version: 1.0.0
author: Jarvis
license: MIT
---

# Deep Research Skill

Conduct comprehensive, multi-phase technical research using a systematic approach inspired by the terra-medium analytical framework. This skill combines web research capabilities with programmatic fallback strategies for robust information gathering.

## Overview

This skill performs deep-dive research across multiple dimensions:
- **Breadth**: Multiple sources and perspectives
- **Depth**: Detailed analysis of key topics
- **Verification**: Cross-referencing and fact-checking
- **Synthesis**: Structured compilation of findings

## Research Phases

### Phase 1: Discovery & Scoping
**Goal**: Define the research boundaries and identify key sources

```python
# Discovery queries
queries = [
    f"{topic} overview",
    f"{topic} current state 2024 2025",
    f"{topic} academic research",
    f"{topic} industry applications"
]

# Source identification
sources = [
    "wikipedia.org",           # Baseline knowledge
    "arxiv.org",               # Academic papers
    "github.com",              # Implementation examples
    "reddit.com/r/[relevant]", # Community insights
    "medium.com/[topic]",      # Technical articles
]
```

### Phase 2: Multi-Source Collection
**Goal**: Gather information from diverse sources using both web tools and fallback methods

#### Method A: Web Tools (Primary)
```python
# Using browser_navigate + browser_snapshot
browser_navigate("https://arxiv.org/search/?query=quantum+optics")
browser_snapshot()  # Extract search results
browser_vision(question="Extract paper titles and abstracts")
```

#### Method B: Fallback Methods (When web tools unavailable)
```python
import urllib.request
import re
import json

def collect_from_sources(topics, sources):
    """Collect data using multiple fallback strategies"""
    results = {}
    
    for topic in topics:
        results[topic] = {
            "sources": {},
            "extracted_data": {},
            "verification": {}
        }
    
    # Strategy 1: Jina AI for article content
    for url in sources:
        article_url = f"https://r.jina.ai/{url}"
        results[topic]["sources"][url] = fetch_article(article_url)
    
    # Strategy 2: DuckDuckGo API for search results
    for query in queries:
        ddg_url = f"https://api.duckduckgo.com/?q={query}&format=json"
        results[topic]["sources"][query] = fetch_ddg_api(ddg_url)
    
    # Strategy 3: GitHub raw for code repositories
    for repo in repositories:
        url = f"https://raw.githubusercontent.com/{repo}/main/README.md"
        results[topic]["sources"][url] = fetch_github(url)
    
    return results
```

### Phase 3: Analysis & Synthesis
**Goal**: Extract key insights and structure findings

#### Key Analysis Dimensions

| Dimension | Questions | Method |
|-----------|-----------|--------|
| **Technical** | How does it work? | Code analysis, documentation |
| **Practical** | How to implement? | Tutorials, examples, guides |
| **Theoretical** | Why does it matter? | Academic papers, theory |
| **Comparative** | How does it compare? | Benchmarks, reviews |
| **Future** | What's next? | Roadmaps, predictions |

#### Structured Output Template

```python
def synthesize_findings(raw_data):
    """Create structured research output"""
    
    output = {
        "executive_summary": {
            "key_findings": [],
            "conclusions": [],
            "recommendations": []
        },
        "technical_details": {
            "architecture": {},
            "implementation": {},
            "performance": {}
        },
        "sources": {
            "academic": [],
            "practical": [],
            "community": []
        },
        "verification": {
            "cross_referenced": [],
            "conflicts": [],
            "gaps": []
        },
        "appendix": {
            "code_samples": [],
            "formulas": [],
            "diagrams": []
        }
    }
    
    return output
```

### Phase 4: Verification & Quality Control
**Goal**: Ensure accuracy and completeness

#### Verification Checklist

- [ ] **Source Diversity**: Information from 3+ independent sources
- [ ] **Temporal Relevance**: Sources from last 12-24 months
- [ ] **Expert Consensus**: Multiple experts agree on key points
- [ ] **Technical Validation**: Code/examples actually work
- [ ] **Mathematical Accuracy**: Formulas verified
- [ ] **Completeness**: No major gaps in critical areas

## Terra-Medium Analytical Framework

The "terra-medium" approach emphasizes:

### 1. Balanced Perspective
Avoid extremes; seek the "middle ground" between:
- Theoretical optimality vs practical feasibility
- Academic rigor vs industry pragmatism
- Cutting-edge novelty vs proven reliability

### 2. Multi-Dimensional Analysis

```
Research Dimensions:
├── Technical Feasibility (0-10)
├── Practical Utility (0-10)
├── Academic Significance (0-10)
├── Implementation Cost (0-10)
└── Future Potential (0-10)

Weighted Score = Σ(dimensions × weights)
```

### 3. Evidence-Based Conclusions

Every claim must be backed by:
- Primary source (original research/documentation)
- Secondary source (analysis/commentary)
- Practical verification (code/test results)

## Implementation Example

### Complete Research Workflow

```python
import urllib.request
import json
import re
from datetime import datetime

class DeepResearchEngine:
    """Multi-phase deep research engine"""
    
    def __init__(self, topic):
        self.topic = topic
        self.results = {}
        self.sources = {}
        
    def phase1_discovery(self):
        """Define research scope and identify sources"""
        print(f"=== Phase 1: Discovery ===")
        
        discovery_queries = [
            f"{self.topic} overview",
            f"{self.topic} 2024 2025",
            f"{self.topic} implementation",
            f"{self.topic} research paper"
        ]
        
        # Collect initial findings
        for query in discovery_queries:
            self.sources[query] = self.fetch_ddg(query)
        
        return self.sources
    
    def phase2_collection(self):
        """Gather data from multiple sources"""
        print(f"=== Phase 2: Collection ===")
        
        sources = [
            f"https://en.wikipedia.org/wiki/{self.topic}",
            f"https://r.jina.ai/http://arxiv.org/search/?query={self.topic}",
            f"https://r.jina.ai/http://github.com/search?q={self.topic}"
        ]
        
        for url in sources:
            try:
                self.sources[url] = self.fetch_url(url)
            except Exception as e:
                print(f"Failed to fetch {url}: {e}")
        
        return self.sources
    
    def phase3_analysis(self):
        """Extract and synthesize key insights"""
        print(f"=== Phase 3: Analysis ===")
        
        # Pattern extraction
        patterns = {
            "key_findings": self.extract_findings(),
            "implementation_steps": self.extract_steps(),
            "performance_metrics": self.extract_metrics(),
            "challenges": self.extract_challenges()
        }
        
        return patterns
    
    def phase4_verification(self):
        """Cross-reference and validate findings"""
        print(f"=== Phase 4: Verification ===")
        
        verification = {
            "cross_referenced": self.cross_reference(),
            "conflicts": self.identify_conflicts(),
            "confidence_score": self.calculate_confidence()
        }
        
        return verification
    
    def fetch_ddg(self, query):
        """Fetch from DuckDuckGo API"""
        url = f"https://api.duckduckgo.com/?q={query.replace(' ', '+')}&format=json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode()).get('Abstract', '')
    
    def fetch_url(self, url):
        """Fetch URL using Jina AI"""
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(jina_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(jina_url, timeout=60) as response:
            return response.read().decode()
    
    def run(self):
        """Execute full research pipeline"""
        self.phase1_discovery()
        self.phase2_collection()
        self.phase3_analysis()
        self.phase4_verification()
        
        return self.results
```

## Usage

### Quick Start

```python
from deep_research import DeepResearchEngine

# Basic usage
engine = DeepResearchEngine("quantum computing applications")
results = engine.run()

# Access findings
print(results['executive_summary']['key_findings'])
```

### Custom Configuration

```python
engine = DeepResearchEngine(
    topic="topic",
    phases=[1, 2, 3],  # Run only specific phases
    sources={
        "custom": "https://example.com"
    },
    max_depth=3  # How deep to go into rabbit holes
)
```

## Output Format

The skill returns a structured JSON object:

```json
{
  "meta": {
    "topic": "...",
    "timestamp": "ISO8601",
    "duration_seconds": 120,
    "sources_accessed": 15
  },
  "executive_summary": {
    "headline": "...",
    "key_points": [...],
    "conclusion": "..."
  },
  "detailed_findings": {
    "section1": {...},
    "section2": {...}
  },
  "sources": {
    "academic": [...],
    "practical": [...],
    "community": [...]
  },
  "verification": {
    "confidence_score": 0.85,
    "cross_referenced": 12,
    "conflicts_resolved": 2
  },
  "recommendations": [
    "Actionable recommendation 1",
    "Actionable recommendation 2"
  ]
}
```

## Best Practices

1. **Start Broad**: Begin with overview queries before diving deep
2. **Cross-Reference**: Never trust a single source
3. **Time-Aware**: Prefer recent sources but acknowledge classics
4. **Practical Focus**: Balance theory with implementation
5. **Document Process**: Track which sources informed which conclusions
6. **Iterate**: Run multiple times with different parameters

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Timeout on source" | Reduce timeout or use shorter queries |
| "No results found" | Broaden query or use alternative source |
| "Conflicting information" | Increase source diversity, look for consensus |
| "Slow extraction" | Process in parallel, reduce max_depth |

## References

- [web-research skill](../web-research/SKILL.md) - Web-based research
- [technical-research-fallback skill](../technical-research-fallback/SKILL.md) - Programmatic research
- [terra-medium framework](../memories/terra-medium.md) - Analytical approach

---

**Created:** 2026-07-27  
**Updated:** 2026-07-27 (Initial version with multi-phase research workflow)