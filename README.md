# Alexa+ Memory Bridge

This is my submission for the Alexa+ track of the Amazon "Build, Ship, Shape" Developer Hackathon. 

The goal of this project is to solve a simple problem: Alexa often forgets things between conversations. I built a self-hosted MCP (Model Context Protocol) server that acts as a shared memory for a household. It also includes a web-based simulator so you can chat with the assistant and watch the MCP server work in real-time.

## How it works

The project has two main parts:
1. **The MCP Server**: A Python backend using SQLite. It implements the MCP spec 2025-11-25 over Streamable HTTP with OAuth 2.1 authentication (static bearer token for local testing).
2. **The Simulator**: A web interface that acts as an MCP client. It sends user prompts to the server and displays the raw JSON-RPC tool calls on the right side of the screen.

## The Tools

The MCP server gives Alexa+ three tools to use:
* `save_household_fact`: Saves a piece of information (content, category, visibility, added_by).
* `recall_fact`: Searches the database using a keyword query.
* `forget_fact`: Deletes a saved fact by its ID.

## How to run it

You will need Python 3.9+ and two separate terminal windows.

**Terminal 1: Start the MCP Server**
```bash
# Run this from the root directory
$env:FITLOG_DB="$PWD\fitlog.db"
$env:FITLOG_API_TOKEN="devtoken"
python server.py