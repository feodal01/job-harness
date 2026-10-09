from src.mcp.server import create_mcp

if __name__ == "__main__":
    create_mcp().run(transport="stdio")
