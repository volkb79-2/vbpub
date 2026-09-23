#!/usr/bin/env python3
"""
Pre-compose hook: Render nginx.conf from Jinja2 template for voice2text reverse-proxy
"""
from pathlib import Path

import tomllib

try:
    from jinja2 import Template
except ImportError:
    print("[ERROR] jinja2 not found", file=sys.stderr)
    sys.exit(1)


STACK_CONFIG_ACTIVE = "ciu.toml"


class PreComposeHook:
    """Pre-compose hook to render nginx.conf from template"""
    
    def __init__(self, env: dict = None):
        """Initialize hook"""
        self.env = env or {}
        self.script_dir = Path(__file__).parent
    
    def run(self, env: dict) -> dict:
        """Execute hook - render nginx configuration"""
        print("[INFO] Rendering nginx.conf from template...")
        
        # Load active configuration
        config_file = self.script_dir / STACK_CONFIG_ACTIVE
        if not config_file.exists():
            print(f"[ERROR] {STACK_CONFIG_ACTIVE} not found", file=sys.stderr)
            print(f"[ERROR] Run ciu.py first", file=sys.stderr)
            sys.exit(1)
        
        try:
            with open(config_file, 'rb') as f:
                config = tomllib.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to load config: {e}", file=sys.stderr)
            sys.exit(1)
        
        return pre_compose_hook(config, env)


def pre_compose_hook(config: dict, env: dict) -> dict:
    """Render nginx.conf from template"""
    script_dir = Path(__file__).parent
    template_file = script_dir / "etc-nginx" / "nginx.conf.j2"
    output_file = script_dir / "etc-nginx" / "nginx.conf"
    
    if not template_file.exists():
        print(f"[ERROR] Template {template_file} not found", file=sys.stderr)
        return {}
    
    try:
        # Load template
        with open(template_file, 'r') as f:
            template = Template(f.read())
        
        # Render with config
        output = template.render(**config)
        
        # Write output
        with open(output_file, 'w') as f:
            f.write(output)
        
        print(f"[INFO] Successfully rendered {output_file}")
        
    except Exception as e:
        print(f"[ERROR] Rendering failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    return {}


if __name__ == '__main__':
    """Standalone test mode"""
    script_dir = Path(__file__).parent
    config_file = script_dir / STACK_CONFIG_ACTIVE
    
    if not config_file.exists():
        print(f"[ERROR] {STACK_CONFIG_ACTIVE} not found", file=sys.stderr)
        sys.exit(1)
    
    with open(config_file, 'rb') as f:
        config = tomllib.load(f)
    
    pre_compose_hook(config, {})
    print("[SUCCESS] nginx.conf rendered")
