"""Variable Renderer — substitute {var} placeholders in prompt templates."""
from __future__ import annotations

import re
from pathlib import Path


def render(
    template: str,
    variables: dict,
    base_dir: str | None = None,
) -> str:
    """Render a prompt template by substituting {var} placeholders.
    
    Args:
        template: Text with {variable} placeholders.
        variables: Dict of variable name → value.
        base_dir: Base directory for resolving {.pipeline/...} file references.
    
    Returns:
        Rendered string with all variables substituted.
    
    Raises:
        KeyError: If a {variable} is not found in variables and is not a file ref.
    """
    # Step 1: Handle escaped braces {{ }} → literal { }
    # Process BEFORE variable substitution so {{var}} is not treated as {var}.
    template = template.replace("{{", "\x00LBRACE\x00").replace("}}", "\x00RBRACE\x00")
    
    # Strategy: find all {...} spans, replace left to right, track string offsets.
    # This avoids re-matching injected JSON content that contains braces.
    
    # Pattern: match {.pipeline/...} and {variable} forms
    # Variable names must be valid identifiers (no spaces)
    pattern = re.compile(r"\{([^}]+)\}")
    
    result = []
    last_end = 0
    
    for match in pattern.finditer(template):
        # Append text before this match
        result.append(template[last_end:match.start()])
        
        var_name = match.group(1)
        
        if var_name.startswith(".pipeline/"):
            # File reference
            full_path = Path(base_dir) / var_name if base_dir else Path(var_name)
            if full_path.exists():
                result.append(full_path.read_text())
            else:
                result.append(f"[file not found: {var_name}]")
        elif var_name in variables and not any(c.isspace() for c in var_name):
            val = variables[var_name]
            result.append("" if val is None else str(val))
        else:
            # Unknown variable — preserve original, warn user
            import logging
            logging.getLogger("cc_pipeline.render").warning(
                "Unknown variable {%s} in prompt — kept as-is (not replaced)", var_name
            )
            result.append(match.group(0))
        
        last_end = match.end()
    
    # Append remaining text after last match
    result.append(template[last_end:])
    
    rendered = "".join(result)
    
    # Step 2: Restore escaped braces as literal braces
    rendered = rendered.replace("\x00LBRACE\x00", "{").replace("\x00RBRACE\x00", "}")
    
    return rendered
