"""
Context building utilities for repository-aware enhancements
"""

from typing import List, Dict, Any, Optional
import json
import structlog

logger = structlog.get_logger()

async def build_context_prompt(
    base_text: str,
    repository_context: str,
    intent: str = "code"
) -> str:
    """Build context-aware prompt for LLM enhancement"""
    
    context_templates = {
        "code": """You are an expert programming assistant helping to clean up speech-to-text output for code development.

Repository Context (technical terms and patterns found in codebase):
{context}

Your task is to fix common speech recognition errors in programming contexts:
- Fix technical terms using the repository context above
- Correct programming keywords and syntax
- Fix variable names, method calls, and type names based on codebase patterns
- Preserve the developer's intent while making the text technically accurate
- Use exact naming conventions found in the repository context

Original speech-to-text: {text}

Provide only the corrected text without explanations or additional formatting:""",

        "comment": """You are helping to improve speech-to-text output for code comments.

Repository Context (technical terms from codebase):
{context}

Fix common errors while maintaining natural language flow for comments:
- Correct technical terminology using repository context
- Fix grammar and punctuation
- Maintain conversational tone appropriate for code comments
- Use proper technical terms as they appear in the codebase

Original speech-to-text: {text}

Provide only the improved comment text:""",

        "documentation": """You are helping to create technical documentation from speech-to-text.

Repository Context (technical terms and patterns):
{context}

Enhance the text for documentation purposes:
- Fix technical terms and concepts using repository context
- Improve clarity and structure
- Use consistent terminology from the codebase
- Maintain professional documentation tone

Original speech-to-text: {text}

Provide the enhanced documentation text:"""
    }
    
    template = context_templates.get(intent, context_templates["code"])
    
    # Truncate context if too long
    if len(repository_context) > 2000:
        repository_context = repository_context[:2000] + "..."
        logger.debug("Repository context truncated", original_length=len(repository_context))
    
    prompt = template.format(
        context=repository_context or "No repository context available",
        text=base_text
    )
    
    logger.debug("Context prompt built",
                intent=intent,
                prompt_length=len(prompt),
                context_length=len(repository_context))
    
    return prompt

async def extract_technical_terms(text: str) -> List[str]:
    """Extract potential technical terms from text"""
    
    import re
    
    technical_patterns = [
        r'\b[A-Z][a-zA-Z]*[A-Z][a-zA-Z]*\b',  # CamelCase
        r'\b[a-z]+[A-Z][a-zA-Z]*\b',          # camelCase
        r'\b[a-z_]+_[a-z_]+\b',               # snake_case
        r'\b[A-Z_]+\b',                       # CONSTANTS
        r'\b\w+\(\)',                         # function calls
        r'\b\w+\.\w+\b',                      # method calls
    ]
    
    terms = set()
    
    for pattern in technical_patterns:
        matches = re.findall(pattern, text)
        terms.update(matches)
    
    # Filter out common words
    common_words = {
        'API', 'URL', 'HTTP', 'JSON', 'XML', 'HTML', 'CSS', 'JS',
        'true', 'false', 'null', 'undefined', 'void', 'string', 'number'
    }
    
    technical_terms = [term for term in terms if len(term) > 2 and term not in common_words]
    
    logger.debug("Technical terms extracted",
                terms_count=len(technical_terms),
                sample_terms=technical_terms[:10])
    
    return technical_terms

async def build_vocabulary_map(
    repository_terms: List[str],
    common_misrecognitions: Dict[str, str]
) -> Dict[str, str]:
    """Build vocabulary correction map"""
    
    # Common speech-to-text errors for programming terms
    default_corrections = {
        # Programming languages
        "java script": "JavaScript",
        "type script": "TypeScript", 
        "pie thon": "Python",
        "see sharp": "C#",
        "see plus plus": "C++",
        
        # Common frameworks
        "react": "React",
        "view": "Vue",
        "anger": "Angular",
        "no js": "Node.js",
        "next js": "Next.js",
        
        # Common terms
        "jay son": "JSON",
        "A P I": "API",
        "H T T P": "HTTP",
        "U R L": "URL",
        "S Q L": "SQL",
        "G I T": "Git",
        
        # Programming concepts
        "function": "function",
        "variable": "variable",
        "array": "array",
        "object": "object",
        "class": "class",
        "method": "method",
        "property": "property",
        
        # Common misrecognitions
        "funk shun": "function",
        "very able": "variable",
        "a ray": "array",
        "ob ject": "object",
        "meth od": "method",
    }
    
    # Merge with repository-specific terms
    vocabulary_map = default_corrections.copy()
    vocabulary_map.update(common_misrecognitions)
    
    # Add repository terms (exact matches)
    for term in repository_terms:
        # Add lowercase version mapping to proper case
        vocabulary_map[term.lower()] = term
        
        # Add space-separated version for compound terms
        if len(term) > 6:  # Only for longer terms
            spaced_version = ' '.join(re.findall(r'[A-Z][a-z]*|[a-z]+', term))
            if spaced_version.lower() != term.lower():
                vocabulary_map[spaced_version.lower()] = term
    
    logger.info("Vocabulary map built",
               total_mappings=len(vocabulary_map),
               repository_terms=len(repository_terms),
               default_corrections=len(default_corrections))
    
    return vocabulary_map

async def apply_vocabulary_corrections(
    text: str,
    vocabulary_map: Dict[str, str]
) -> str:
    """Apply vocabulary corrections to text"""
    
    corrected_text = text
    corrections_made = 0
    
    # Sort by length (longer phrases first) to avoid partial replacements
    sorted_terms = sorted(vocabulary_map.keys(), key=len, reverse=True)
    
    for incorrect_term in sorted_terms:
        correct_term = vocabulary_map[incorrect_term]
        
        # Case-insensitive replacement
        import re
        pattern = re.compile(re.escape(incorrect_term), re.IGNORECASE)
        matches = pattern.findall(corrected_text)
        
        if matches:
            corrected_text = pattern.sub(correct_term, corrected_text)
            corrections_made += len(matches)
    
    logger.debug("Vocabulary corrections applied",
                corrections_made=corrections_made,
                original_length=len(text),
                corrected_length=len(corrected_text))
    
    return corrected_text

async def format_context_summary(context_data: Dict[str, Any]) -> str:
    """Format repository context for LLM prompt"""
    
    summary_parts = []
    
    # Technical terms
    if "technical_terms" in context_data:
        terms = context_data["technical_terms"][:50]  # Limit to top 50
        summary_parts.append(f"Technical Terms: {', '.join(terms)}")
    
    # Function/method names
    if "functions" in context_data:
        functions = context_data["functions"][:20]  # Limit to top 20
        summary_parts.append(f"Functions/Methods: {', '.join(functions)}")
    
    # Class/type names
    if "classes" in context_data:
        classes = context_data["classes"][:20]
        summary_parts.append(f"Classes/Types: {', '.join(classes)}")
    
    # Variable patterns
    if "variables" in context_data:
        variables = context_data["variables"][:15]
        summary_parts.append(f"Variable Patterns: {', '.join(variables)}")
    
    # File types/languages
    if "languages" in context_data:
        languages = context_data["languages"]
        summary_parts.append(f"Languages: {', '.join(languages)}")
    
    context_summary = "\n".join(summary_parts)
    
    logger.debug("Context summary formatted",
                parts=len(summary_parts),
                summary_length=len(context_summary))
    
    return context_summary