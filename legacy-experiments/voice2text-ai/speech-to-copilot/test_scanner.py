"""
Simple repository scanner test
"""

async def test_repository_functionality():
    """Test repository scanner functionality"""
    
    # Test 1: Basic import
    print("Testing imports...")
    try:
        from services.repository_scanner import RepositoryScanner
        print("✓ RepositoryScanner import successful")
    except Exception as e:
        print(f"✗ RepositoryScanner import failed: {e}")
        return False
    
    # Test 2: Instance creation
    print("Testing instance creation...")
    try:
        scanner = RepositoryScanner("/tmp")
        print("✓ Scanner instance created")
    except Exception as e:
        print(f"✗ Scanner instance creation failed: {e}")
        return False
    
    # Test 3: Health check
    print("Testing health check...")
    try:
        health = await scanner.health_check()
        print(f"✓ Health check result: {health}")
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False
    
    # Test 4: Context retrieval
    print("Testing context retrieval...")
    try:
        context = await scanner.get_context(['python'], 5)
        print(f"✓ Context retrieved: {context}")
    except Exception as e:
        print(f"✗ Context retrieval failed: {e}")
        return False
    
    print("All tests passed!")
    return True

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_repository_functionality())