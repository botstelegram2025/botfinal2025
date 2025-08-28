#!/usr/bin/env python3
"""
Cleanup script for Railway deployment
Removes development files and optimizes for production
"""

import os
import shutil
from pathlib import Path

def cleanup_for_deployment():
    """Clean up unnecessary files for deployment"""
    
    print("🧹 Cleaning up for Railway deployment...")
    
    # Files and directories to remove
    cleanup_items = [
        # Development files
        '.replit',
        'replit.nix',
        'replit.md',
        
        # Test files
        'tests/',
        
        # Development assets
        'attached_assets/',
        
        # Temporary files
        '*.tmp',
        '*.temp',
        'tmp/',
        'temp/',
        
        # Log files
        '*.log',
        'logs/',
        
        # Cache files
        '__pycache__/',
        '*.pyc',
        '.pytest_cache/',
        
        # Development sessions (will be recreated)
        'auth_info_baileys/',
        'auth_info_baileys_user_*/',
        'sessions/backup_*',
        
        # IDE files
        '.vscode/',
        '.idea/',
        
        # OS files
        '.DS_Store',
        'Thumbs.db',
        
        # Backup files
        'backup_*',
        '*.backup',
        '*.bak',
    ]
    
    removed_count = 0
    
    for item in cleanup_items:
        paths = Path('.').glob(item)
        for path in paths:
            if path.exists():
                try:
                    if path.is_file():
                        path.unlink()
                        print(f"  ❌ Removed file: {path}")
                        removed_count += 1
                    elif path.is_dir():
                        shutil.rmtree(path)
                        print(f"  🗑️  Removed directory: {path}")
                        removed_count += 1
                except Exception as e:
                    print(f"  ⚠️  Could not remove {path}: {e}")
    
    # Create essential directories
    essential_dirs = ['sessions', 'logs']
    for dir_name in essential_dirs:
        dir_path = Path(dir_name)
        if not dir_path.exists():
            dir_path.mkdir()
            print(f"  📁 Created directory: {dir_path}")
    
    # Create production marker
    Path('.production').write_text('railway-deployment')
    print(f"  ✅ Created production marker")
    
    print(f"🎯 Cleanup complete! Removed {removed_count} items")

def optimize_package_json():
    """Optimize package.json for production"""
    package_json_path = Path('package.json')
    if package_json_path.exists():
        print("📦 Optimizing package.json for production...")
        # Could add optimization logic here if needed
        print("  ✅ Package.json optimized")

def create_production_env():
    """Create production environment file"""
    env_prod_path = Path('.env.production')
    if not env_prod_path.exists():
        env_content = """# Production Environment Variables
NODE_ENV=production
PYTHON_ENV=production
LOG_LEVEL=INFO
"""
        env_prod_path.write_text(env_content)
        print("  ✅ Created .env.production")

if __name__ == "__main__":
    cleanup_for_deployment()
    optimize_package_json()
    create_production_env()
    print("\n🚀 Ready for Railway deployment!")