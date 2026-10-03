# 🚀 Learn → Create → Review → Publish

### LinkedIn Learning Publisher MCP

An MCP server that turns my daily learning into a LinkedIn post with a technical visual — while keeping me in control of publishing.

🎥 Watch Demo Video => https://drive.google.com/file/d/1nlbPfsBOgvjnr5hbVK8sIiO_j7FneRgB/view?usp=sharing

## 📂 Project Structure

```text
linkedin-learning-publisher-v2/
│
├── server.py                    # MCP server
├── daily_notes.txt              # Daily learning notes
├── README.md                    # Project documentation
├── manifest.json                # MCPB configuration
├── pyproject.toml               # Project dependencies
├── requirements.txt             # Python dependencies
├── .gitignore                   # Git ignored files
├── .mcpbignore                 # MCPB package exclusions
│
├── tests/
│   └── test_server.py           # Automated tests
│
├── generated_images/            # Generated technical diagrams
│
└── user_data/
    ├── pending_post/            # Current LinkedIn draft
    └── archive/                 # Published/cancelled drafts
    

## 💡 How It Works

```text
Daily Learning
      ↓
    Claude
      ↓
Caption + Technical Diagram
      ↓
  Review Draft
      ↓
"Publish it"
      ↓
   LinkedIn

✨ Features
- 📝 Reads learning from daily_notes.txt
- 🤖 Generates a natural LinkedIn caption
- 🎨 Creates technical diagrams locally using SVG → PNG
- 📋 Saves posts as pending drafts
- 🔄 Regenerate image or complete post
- ❌ Cancel drafts safely
- 🔐 Requires explicit confirmation before publishing
- 🌐 Publishes through Playwright
- 🚫 No OpenAI API key or paid image API required
- 📦 Supports Claude Desktop MCPB

🧩 MCP Components
Type	Name	Purpose
Resource	learnings://today/raw	Reads today's learning
Resource	draft://pending	Reads pending draft
Prompt	format_linkedin_post	Formats the LinkedIn post
Tool	generate_linkedin_image	Creates technical visual
Tool	save_pending_draft	Saves the draft
Tool	get_pending_draft	Views the draft
Tool	cancel_pending_draft	Cancels the draft
Tool	post_to_linkedin	Publishes to LinkedIn


🛠️ Tech Stack
Python · FastMCP · Claude Desktop · Playwright · SVG · uv
🚀 Run Locally
uv sync
uv run playwright install chromium
uv run server.py

Add your learning to:
daily_notes.txt

Then ask Claude:
Create today's LinkedIn post.

Review the draft and, when you're ready:
Publish it.

🎯 Why I Built This
I wanted to go beyond a basic MCP thoery and build something I could actually use.
This project helped me understand how MCP Tools, Resources and Prompts can work together with browser automation to turn a simple learning note into a real-world action.

👨‍💻 Built by Gaurav Deore
⭐ If you find it useful, feel free to explore the project and give it a star.

This is much more suitable for your GitHub repo: **short enough to read quickly, but still shows the architecture, MCP components, demo, screenshots, and how to run it.