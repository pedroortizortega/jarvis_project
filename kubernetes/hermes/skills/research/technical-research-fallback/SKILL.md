---
name: technical-research-fallback
description: Use urllib and APIs for research when browser tools fail, including quantum optics lab equipment research
tags: [research, web, fallback, technical, data-extraction, quantum-optics, lab-equipment]
version: 1.0.0
author: Jarvis
license: MIT
---

# Technical Research with Fallback Tools

Conduct in-depth technical research when browser tools (agent-browser) are unavailable or fail, using programmatic HTTP requests and content extraction.

## When to Use

| Situation | Action |
|-----------|--------|
| Chrome not installed | Use urllib + Jina AI |
| Browser blocked by network | Use curl/wget |
| Need structured data extraction | Use API endpoints (DuckDuckGo, Wikipedia) |
| Need article content extraction | Use Jina AI (r.jina.ai) |
| GitHub README access | Use raw.githubusercontent.com |

## Core Techniques

### 1. Jina AI Article Extraction

```python
import urllib.request

def fetch_article(url):
    jina_url = f"https://r.jina.ai/{url}"
    req = urllib.request.Request(jina_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode()

content = fetch_article("https://en.wikipedia.org/wiki/Quantum_optics")
```

**Advantages:**
- Clean markdown output (HTML stripped)
- No JavaScript execution needed
- Works on static Wikipedia pages
- Handles most article formats

### 2. DuckDuckGo JSON API

```python
import json

def search_duckduckgo(query):
    url = f"https://api.duckduckgo.com/?q={query}&format=json&iax=1"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())

result = search_duckduckgo("quantum optics components")
print(result.get('Abstract', ''))
```

### 3. GitHub Raw Content

```python
def fetch_github_readme(repo, path="README.md"):
    url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode()

content = fetch_github_readme("yallain/EduQ-V1")
```

### 4. Multi-Source Batch Research

```python
import urllib.request
import re

def batch_research(topics):
    results = {}
    for topic in topics:
        print(f"=== Researching: {topic} ===")
        
        sources = [
            f"https://r.jina.ai/http://duckduckgo.com/html/?q={topic.replace(' ', '+')}",
            f"https://r.jina.ai/http://wikipedia.org/wiki/{topic.replace(' ', '_')}"
        ]
        
        for source in sources:
            try:
                req = urllib.request.Request(source, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    html = response.read().decode()
                    text = re.sub(r'<[^>]+>', ' ', html)
                    text = re.sub(r'\s+', '\n', text)
                    results[topic] = text[:2000]
                    break
            except:
                continue
        
        if not results.get(topic):
            results[topic] = "No content extracted"
    
    return results
```

## Pitfalls and Workarounds

### Problem: JSON parsing fails with control characters

```python
from hermes_tools import json_parse
data = json_parse(html_content)
```

### Problem: 403 Forbidden on direct website access

Use search results instead: duckduckgo.com/html/?q=...

### Problem: Timeout on slow responses

```python
with urllib.request.urlopen(url, timeout=60) as response:
    ...
```

### Problem: HTML entities not decoded

```python
import html
text = html.unescape(text)
```

## Best Practices

1. Always use User-Agent: Mozilla/5.0 - Many servers block requests without proper headers
2. Set reasonable timeouts (30-60s) - Prevent hanging on slow connections
3. Extract content incrementally - Do not wait for full response before processing
4. Cross-reference multiple sources - Verify findings across 2+ sources
5. Use regex to extract key data - Patterns like prices, specifications, URLs
6. Document extraction failures - Note which sources returned empty/error

## Reference Pattern: Component Research

```python
import urllib.request
import re
import json

def research_components(topic):
    """Research technical components with multiple fallback strategies"""
    
    queries = [
        f"{topic} components list",
        f"{topic} DIY setup",
        f"{topic} equipment suppliers"
    ]
    
    findings = {}
    
    for query in queries:
        ddg_url = f"https://api.duckduckgo.com/?q={query.replace(' ', '+')}&format=json"
        try:
            req = urllib.request.Request(ddg_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())
                findings[query] = data.get('Abstract', '')
        except:
            pass
    
    for query in queries[:1]:
        article_url = f"https://r.jina.ai/http://duckduckgo.com/html/?q={query.replace(' ', '+')}"
        try:
            req = urllib.request.Request(article_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read().decode()
                text = re.sub(r'<[^>]+>', ' ', content)
                findings[f"article_{query}"] = text[:3000]
        except:
            pass
    
    return findings
```

## Example Use Cases

| Use Case | Method | Example |
|----------|--------|---------|
| Research quantum optics components | Jina AI + DuckDuckGo | EduQ V1 platform analysis |
| Find open-source project details | GitHub raw | Thorlabs API documentation |
| Compare product specifications | Multiple sources | Laser source comparison |
| Extract pricing information | Search results | Component cost analysis |
| Document research process | Batch script | Automated research workflow |

## Quick Reference

| Tool | URL Pattern | Best For |
|------|-------------|----------|
| Jina AI | https://r.jina.ai/{url} | Article content extraction |
| DuckDuckGo API | https://api.duckduckgo.com/?q={query}&format=json | Search results, abstracts |
| GitHub Raw | https://raw.githubusercontent.com/{repo}/{path} | README, documentation |
| Wikipedia | https://en.wikipedia.org/wiki/{page} | Encyclopedic information |
| Jina AI + DDG | https://r.jina.ai/http://duckduckgo.com/html/?q={query} | Article-style search results |

---

**Created:** 2026-07-27  
**Updated:** 2026-07-27 (based on quantum optics lab research session)
