"""
LinkedIn Learning Publisher MCP Server (V2)

A local, AI-powered MCP server for Claude Desktop and Playwright:
- Resources:
    * learnings://today/raw (today's learning notes from daily_notes.txt)
    * draft://pending (current pending LinkedIn post draft)
- Prompts:
    * format_linkedin_post (formats learning into caption + technical SVG diagram prompt)
- Tools:
    * generate_linkedin_image(image_prompt: str = "", svg_content: str = "") -> str
    * save_pending_draft(caption: str = "", image_path: str = "", learning_source: str = "") -> str
    * get_pending_draft() -> str
    * cancel_pending_draft() -> str
    * post_to_linkedin(content: str = "", image_path: str = "") -> str

No external image-generation APIs or API keys required.
Technical diagrams are created locally as SVG and rendered to PNG via Playwright.
"""

import asyncio
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Initialize FastMCP Server
mcp = FastMCP("LinkedInLearningPublisher")

# Local paths
BASE_DIR = Path(__file__).parent.resolve()
DAILY_NOTES_FILE = BASE_DIR / "daily_notes.txt"
USER_DATA_DIR = BASE_DIR / "user_data"
PENDING_POST_DIR = USER_DATA_DIR / "pending_post"
DRAFT_ARCHIVE_DIR = USER_DATA_DIR / "archive"
GENERATED_IMAGES_DIR = BASE_DIR / "generated_images"

# Automatically load .env if present
load_dotenv(BASE_DIR / ".env", override=False)


# ==============================================================================
# SECURITY & UTILITY HELPERS
# ==============================================================================
def _sanitize_error(error: Exception | str) -> str:
    """
    Remove any sensitive tokens or secrets from error strings
    to guarantee that sensitive data is never leaked to logs or users.
    """
    text = str(error)
    text = re.sub(r"sk-[a-zA-Z0-9_\-]{20,}", "[REDACTED_API_KEY]", text)
    text = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [REDACTED_TOKEN]", text)
    return text


def _archive_pending_draft(reason: str = "published") -> None:
    """
    Move the current pending draft to an archive folder with a timestamp.
    Guarantees that a published or cancelled post cannot be accidentally
    re-published, while preserving a permanent local history.
    """
    if not PENDING_POST_DIR.exists():
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = DRAFT_ARCHIVE_DIR / f"{timestamp}_{reason}"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        meta_file = PENDING_POST_DIR / "metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                meta["archived_at"] = datetime.now().isoformat()
                meta["archive_reason"] = reason
                meta["status"] = reason
                meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except Exception:
                pass

        for item in list(PENDING_POST_DIR.iterdir()):
            if item.is_file():
                shutil.move(str(item), str(target_dir / item.name))
        logger.info(f"Archived pending draft to {target_dir}")
    except Exception as e:
        logger.warning(f"Could not archive pending draft: {_sanitize_error(e)}")


# ==============================================================================
# 1. MCP RESOURCE: learnings://today/raw
# ==============================================================================
@mcp.resource("learnings://today/raw")
def get_today_learning() -> str:
    """
    Read-only resource that provides today's saved learning notes.
    Reads daily_notes.txt and returns plain text.
    """
    if not DAILY_NOTES_FILE.exists():
        return "Error: daily_notes.txt is missing. Please create daily_notes.txt with your learning notes."

    try:
        content = DAILY_NOTES_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return "Error: Daily learning in daily_notes.txt is empty. Please add your learning notes before creating a post."
        return content
    except Exception as e:
        return f"Error reading daily notes: {_sanitize_error(e)}"


# ==============================================================================
# 2. MCP RESOURCE: draft://pending
# ==============================================================================
@mcp.resource("draft://pending")
def get_pending_draft_resource() -> str:
    """
    Resource that inspects the current pending LinkedIn post draft.
    Returns caption text, image attachment status, and creation metadata.
    """
    return get_pending_draft()


# ==============================================================================
# 3. MCP PROMPT: format_linkedin_post
# ==============================================================================
@mcp.prompt()
def format_linkedin_post() -> str:
    """
    Prompt template instructing Claude how to convert learning notes into
    a professional LinkedIn caption and a clean, technical SVG visual diagram.
    """
    return (
        "You are an assistant helping create a complete LinkedIn post from today's learning.\n\n"
        "Workflow:\n"
        "1. Read today's learning from learnings://today/raw (or from the user's message).\n"
        "2. Generate two components based ONLY on the learning content:\n"
        "   a. Professional LinkedIn Caption:\n"
        "      - Sound human and authentic, like a student or developer sharing what they learned in public.\n"
        "      - Professional and concise. Avoid robotic wording.\n"
        "      - Short, engaging opening hook.\n"
        "      - Clean breakdown of the core concept and practical takeaways.\n"
        "      - 2-3 relevant hashtags (e.g., #LearningInPublic #AI #Python #Developer).\n"
        "      - STRICT NEGATIVE CONSTRAINTS: Avoid fake achievements, avoid fake statistics, avoid unsupported claims.\n"
        "      - Only use information supported by the actual learning.\n"
        "   b. Concise Technical Image Specification for generate_linkedin_image:\n"
        "      - Describe the workflow diagram, architecture pipeline, or system components.\n"
        "      - Example for RAG: 'User Query -> Retriever -> Relevant Documents -> Context -> LLM -> Response'\n"
        "      - Example for MCP: 'User -> Claude Client -> MCP Protocol -> MCP Server -> Tools & Resources'\n"
        "      - Example for Transformers: 'Input Tokens -> Embeddings -> Self-Attention -> Feed Forward -> Output Probabilities'\n"
        "      - Example for LoRA: 'Base Model (Frozen) + LoRA Adapters -> Forward Pass -> Fine-Tuned Output'\n"
        "      - Or provide custom valid SVG markup directly via svg_content.\n"
        "3. Call generate_linkedin_image(image_prompt=...) to locally generate the SVG and render it to a PNG.\n"
        "4. Call save_pending_draft(caption=..., image_path=...) to save caption + image as a pending draft locally.\n"
        "5. Present the draft caption and image details to the user for review.\n"
        "6. Ask: 'Your LinkedIn post is ready. Would you like me to publish it?'\n"
        "7. STRICT PUBLISHING SAFETY RULE: NEVER publish automatically immediately after generating.\n"
        "   - Do NOT interpret casual affirmations ('Looks good', 'Okay', 'Nice', 'Show me', 'Create it', 'Generate it', 'I like it') as publishing permission.\n"
        "   - Only an explicit publishing request such as 'Publish it', 'Publish this', or 'Post it on LinkedIn' triggers post_to_linkedin().\n"
        "   - If user says 'Regenerate the image' or 'Create another image': call generate_linkedin_image() with a refined prompt while preserving the existing caption.\n"
        "   - If user says 'Regenerate the post': generate a new caption and image, then replace the pending draft.\n"
        "   - If user says 'Don't publish it' or 'Cancel': call cancel_pending_draft() to archive and discard the draft."
    )


# ==============================================================================
# LOCAL SVG GENERATION & PNG CONVERSION
# ==============================================================================
def build_technical_svg(prompt_or_topic: str) -> str:
    """
    Generate a clean, modern, and professional SVG technical diagram based on
    a topic, architectural workflow, or explicit steps.
    If the prompt already contains an <svg> element, returns that SVG.
    """
    raw_text = (prompt_or_topic or "").strip()

    # Check if raw SVG is already provided
    svg_match = re.search(r"<svg[\s\S]*?</svg>", raw_text, re.IGNORECASE)
    if svg_match:
        return svg_match.group(0)

    width, height = 1200, 675
    lower_text = raw_text.lower()
    title = "System Architecture & Workflow"
    subtitle = "Technical pipeline and component interaction architecture"

    # Known specialized topic templates
    steps = []
    if "rag" in lower_text or "retrieval" in lower_text:
        title = "Retrieval-Augmented Generation (RAG)"
        subtitle = "Grounding language models with contextual document retrieval"
        steps = [
            ("User Query", "Input prompt from user"),
            ("Retriever", "Semantic vector search"),
            ("Relevant Docs", "Retrieved knowledge chunks"),
            ("Context Engine", "Prompt + context assembly"),
            ("LLM", "Context-aware inference"),
            ("Response", "Accurate grounded output"),
        ]
    elif "mcp" in lower_text or "model context" in lower_text:
        title = "Model Context Protocol (MCP)"
        subtitle = "Standardized client-server protocol for AI applications"
        steps = [
            ("User / Claude", "AI host interface"),
            ("MCP Client", "Protocol client session"),
            ("JSON-RPC", "Transport layer (stdio/SSE)"),
            ("MCP Server", "Capability provider"),
            ("Tools & Resources", "Tools, Prompts, Data"),
        ]
    elif "transformer" in lower_text or "attention" in lower_text:
        title = "Transformer Architecture"
        subtitle = "Deep self-attention and sequence-to-sequence modeling"
        steps = [
            ("Input Tokens", "Raw text tokenization"),
            ("Embeddings", "Positional + token vectors"),
            ("Multi-Head Attention", "Contextual token relations"),
            ("Feed Forward", "Non-linear transformations"),
            ("Output Layer", "Next-token prediction"),
        ]
    elif "lora" in lower_text or "fine-tuning" in lower_text or "adapter" in lower_text:
        title = "Low-Rank Adaptation (LoRA)"
        subtitle = "Parameter-efficient model adaptation via rank decomposition"
        steps = [
            ("Base Model", "Pre-trained frozen weights"),
            ("Adapter Matrices", "Low-rank A & B matrices"),
            ("Rank Decomposition", "Intrinsic dimension update"),
            ("Fine-Tuned Output", "Specialized domain responses"),
        ]
    elif "vector" in lower_text or "embedding" in lower_text:
        title = "Vector Database & Embeddings"
        subtitle = "High-dimensional similarity indexing and nearest neighbor search"
        steps = [
            ("Raw Content", "Unstructured documents"),
            ("Embedding Model", "Dense vector generation"),
            ("Vector Index", "HNSW / IVF similarity index"),
            ("ANN Search", "Cosine / Euclidean search"),
            ("Top-K Matches", "Ranked nearest vectors"),
        ]
    elif "playwright" in lower_text or "browser" in lower_text:
        title = "Playwright Browser Automation"
        subtitle = "Automated session control and DOM interaction workflow"
        steps = [
            ("MCP Tool Call", "Publish command received"),
            ("Chromium Context", "Persistent profile session"),
            ("Composer DOM", "Editor element discovery"),
            ("File Chooser", "Media upload interception"),
            ("Publish Confirmation", "Modal verification & archive"),
        ]
    else:
        cleaned = re.sub(r"^(workflow|diagram|create|generate|illustration):\s*", "", raw_text, flags=re.I)
        arrow_split = re.split(r"\s*(?:->|→|=>|⇒|↓)\s*", cleaned)
        if len(arrow_split) >= 2:
            extracted_title = arrow_split[0].strip()
            if len(arrow_split) > 2:
                title = f"{extracted_title} Pipeline"
            steps = [(s.strip(), f"Stage {idx+1}") for idx, s in enumerate(arrow_split) if s.strip()]
        else:
            lines = [line_item.strip(" -*0123456789.") for line_item in cleaned.splitlines() if line_item.strip(" -*0123456789.")]
            if len(lines) >= 3:
                steps = [(line_item, f"Step {idx+1}") for idx, line_item in enumerate(lines[:6])]
            else:
                title = cleaned[:45] + ("..." if len(cleaned) > 45 else "")
                steps = [
                    ("Input & Request", "Data ingestion"),
                    ("Processing Core", "Transformation logic"),
                    ("State Validation", "Verification & checks"),
                    ("Output & Result", "Generated artifact"),
                ]

    steps = steps[:6]
    n = len(steps)
    if n == 0:
        steps = [("Input", "Start"), ("Process", "Transform"), ("Output", "Result")]
        n = 3

    start_x = 70
    total_w = 1060
    card_h = 180
    card_y = 250
    gap = 24 if n > 4 else 35
    card_w = int((total_w - (n - 1) * gap) / n)

    accents = ["#38bdf8", "#818cf8", "#34d399", "#f472b6", "#fbbf24", "#a78bfa"]
    nodes_svg = []

    for i, (step_title, step_desc) in enumerate(steps):
        cx = start_x + i * (card_w + gap)
        accent = accents[i % len(accents)]
        num = f"{i+1:02d}"

        disp_title = step_title[:18] + (".." if len(step_title) > 18 else "")
        disp_desc = step_desc[:24] + (".." if len(step_desc) > 24 else "")

        nodes_svg.append(f"""
    <g class="node-card" transform="translate({cx}, {card_y})">
      <rect width="{card_w}" height="{card_h}" rx="12" fill="#1e293b" stroke="#334155" stroke-width="1.5"/>
      <rect width="{card_w}" height="4" rx="2" fill="{accent}"/>
      <circle cx="28" cy="30" r="13" fill="{accent}" fill-opacity="0.15" stroke="{accent}" stroke-width="1.2"/>
      <text x="28" y="34" fill="{accent}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="11" font-weight="700" text-anchor="middle">{num}</text>
      <text x="{card_w // 2}" y="95" fill="#f8fafc" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="15" font-weight="600" text-anchor="middle">{disp_title}</text>
      <text x="{card_w // 2}" y="125" fill="#94a3b8" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="11" text-anchor="middle">{disp_desc}</text>
    </g>""")

        if i < n - 1:
            ax1 = cx + card_w + 3
            ax2 = cx + card_w + gap - 6
            ay = card_y + (card_h // 2)
            nodes_svg.append(f"""
    <line x1="{ax1}" y1="{ay}" x2="{ax2}" y2="{ay}" stroke="#38bdf8" stroke-width="2.5" stroke-dasharray="4,3" marker-end="url(#arrow)"/>""")

    nodes_str = "\n".join(nodes_svg)

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#090d16"/>
      <stop offset="50%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#090d16"/>
    </linearGradient>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#334155" stroke-width="0.5" stroke-opacity="0.25"/>
    </pattern>
    <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#38bdf8"/>
    </marker>
  </defs>

  <!-- Background -->
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <rect width="{width}" height="{height}" fill="url(#grid)"/>

  <!-- Header Badge -->
  <rect x="70" y="48" width="190" height="26" rx="13" fill="#0369a1" fill-opacity="0.2" stroke="#38bdf8" stroke-width="1.2"/>
  <text x="165" y="65" fill="#38bdf8" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="11" font-weight="700" letter-spacing="1.5" text-anchor="middle">TECHNICAL WORKFLOW</text>

  <!-- Header Titles -->
  <text x="70" y="122" fill="#f8fafc" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="28" font-weight="700">{title}</text>
  <text x="70" y="152" fill="#94a3b8" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="14">{subtitle}</text>

  <!-- Divider -->
  <line x1="70" y1="185" x2="1130" y2="185" stroke="#1e293b" stroke-width="1.5"/>

  <!-- Diagram Nodes -->
  {nodes_str}

  <!-- Footer Divider -->
  <line x1="70" y1="585" x2="1130" y2="585" stroke="#1e293b" stroke-width="1.5"/>

  <!-- Footer -->
  <text x="70" y="620" fill="#64748b" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="12">LinkedIn Learning Publisher • Technical Architecture Visual</text>
  <text x="1130" y="620" fill="#64748b" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="12" text-anchor="end">Deterministic SVG &bull; High Resolution PNG</text>
</svg>"""

    return svg_content


async def convert_svg_to_png(svg_string: str, output_png_path: Path, width: int = 1200, height: int = 675) -> Path:
    """
    Render an SVG string into a high-resolution PNG image file locally using Playwright's
    headless Chromium engine. No external image generation APIs or API keys required.
    """
    output_png_path.parent.mkdir(parents=True, exist_ok=True)
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #090d16; width: {width}px; height: {height}px; overflow: hidden; display: flex; align-items: center; justify-content: center; }}
  svg {{ width: {width}px; height: {height}px; display: block; }}
</style>
</head>
<body>
{svg_string}
</body>
</html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.set_content(html_content, wait_until="domcontentloaded")
            await page.wait_for_timeout(100)
            await page.screenshot(path=str(output_png_path), type="png")
        finally:
            await browser.close()

    if not output_png_path.exists() or output_png_path.stat().st_size == 0:
        raise RuntimeError(f"Failed to generate PNG from SVG at {output_png_path}")

    return output_png_path


# ==============================================================================
# 4. MCP TOOL: generate_linkedin_image
# ==============================================================================
@mcp.tool()
async def generate_linkedin_image(image_prompt: str = "", svg_content: str = "") -> str:
    """
    Generate a professional technical diagram for LinkedIn based on today's learning.
    Creates a clean SVG technical diagram locally and converts it to a PNG image.
    Does NOT require OpenAI or any paid external image API.
    Saves the image locally inside generated_images/ and user_data/pending_post/image.png.
    """
    prompt_text = (image_prompt or "").strip()
    custom_svg = (svg_content or "").strip()

    if not prompt_text and not custom_svg:
        return "Error: Image prompt or SVG content cannot be empty."

    try:
        if custom_svg:
            svg_data = build_technical_svg(custom_svg)
        else:
            svg_data = build_technical_svg(prompt_text)

        GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_POST_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        generated_png_path = GENERATED_IMAGES_DIR / f"technical_diagram_{timestamp}.png"
        target_pending_image = PENDING_POST_DIR / "image.png"

        logger.info(f"Generating technical visual locally: {generated_png_path.name}")
        await convert_svg_to_png(svg_data, generated_png_path)

        # Copy to active pending post directory
        shutil.copyfile(str(generated_png_path), str(target_pending_image))

        # Update metadata.json if present
        metadata_file = PENDING_POST_DIR / "metadata.json"
        metadata = {}
        if metadata_file.exists():
            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            except Exception:
                metadata = {}
        metadata["has_image"] = True
        metadata["image_path"] = str(target_pending_image)
        metadata["generated_image_path"] = str(generated_png_path)
        metadata["image_prompt"] = prompt_text or "Custom SVG Diagram"
        metadata["image_type"] = "local_svg_png"
        metadata["image_updated_at"] = datetime.now().isoformat()
        metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        logger.info(f"Technical visual successfully generated and saved to {target_pending_image}")
        return (
            f"Technical diagram generated successfully without external APIs:\n"
            f"- Image path: {target_pending_image}\n"
            f"- Archive copy: {generated_png_path}\n"
            f"- Format: 1200x675 PNG (rendered from local technical SVG)"
        )
    except Exception as e:
        safe_msg = _sanitize_error(e)
        logger.error(f"Local image generation failed: {safe_msg}")
        return f"Error generating image: {safe_msg}"


# ==============================================================================
# 5. MCP TOOL: save_pending_draft
# ==============================================================================
@mcp.tool()
def save_pending_draft(caption: str = "", image_path: str = "", learning_source: str = "") -> str:
    """
    Save a generated caption and optional image as a pending draft awaiting user confirmation.
    Files are stored in user_data/pending_post/.
    If caption is omitted and a draft caption already exists, the existing caption is preserved.
    Records caption, image path, learning source, and draft status.
    """
    PENDING_POST_DIR.mkdir(parents=True, exist_ok=True)
    caption_file = PENDING_POST_DIR / "caption.txt"
    metadata_file = PENDING_POST_DIR / "metadata.json"
    target_image_file = PENDING_POST_DIR / "image.png"

    caption_text = (caption or "").strip()
    if not caption_text:
        if caption_file.exists():
            caption_text = caption_file.read_text(encoding="utf-8").strip()
        else:
            return "Error: Draft caption cannot be empty when creating a new draft."

    caption_file.write_text(caption_text, encoding="utf-8")

    has_image = False
    resolved_image_path = ""
    if image_path and image_path.strip():
        src_path = Path(image_path.strip())
        if src_path.exists() and src_path.is_file():
            if src_path.resolve() != target_image_file.resolve():
                shutil.copyfile(str(src_path), str(target_image_file))
            has_image = True
            resolved_image_path = str(target_image_file)
    elif target_image_file.exists() and target_image_file.stat().st_size > 0:
        has_image = True
        resolved_image_path = str(target_image_file)

    source = (learning_source or "").strip() or "learnings://today/raw (daily_notes.txt)"

    metadata = {
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "learning_source": source,
        "has_caption": True,
        "has_image": has_image,
        "image_path": resolved_image_path if has_image else None,
        "caption_length": len(caption_text),
        "published": False,
    }
    metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    caption_preview = caption_text[:180] + ("..." if len(caption_text) > 180 else "")
    image_status = f"Attached ({resolved_image_path})" if has_image else "None"

    return (
        "Pending draft saved successfully:\n"
        f"- Caption preview: \"{caption_preview}\"\n"
        f"- Image: {image_status}\n"
        f"- Learning Source: {source}\n"
        "- Status: Pending user confirmation.\n\n"
        "Ask the user: 'Your LinkedIn post is ready. Would you like me to publish it?'\n"
        "Do NOT call post_to_linkedin until the user explicitly says 'Publish it' or similar."
    )


# ==============================================================================
# 6. MCP TOOL: get_pending_draft
# ==============================================================================
@mcp.tool()
def get_pending_draft() -> str:
    """
    Retrieve details of the currently pending LinkedIn post draft.
    Returns caption text, image attachment status, learning source, and creation timestamp.
    """
    caption_file = PENDING_POST_DIR / "caption.txt"
    image_file = PENDING_POST_DIR / "image.png"

    if not caption_file.exists() and not image_file.exists():
        return "No active pending draft found. No pending draft exists. Create one by asking Claude to 'Create today\\'s LinkedIn post'."

    try:
        caption = caption_file.read_text(encoding="utf-8").strip() if caption_file.exists() else "(No caption text)"
        has_image = image_file.exists() and image_file.stat().st_size > 0

        metadata_file = PENDING_POST_DIR / "metadata.json"
        created_at = "Unknown"
        learning_source = "learnings://today/raw (daily_notes.txt)"
        if metadata_file.exists():
            try:
                meta = json.loads(metadata_file.read_text(encoding="utf-8"))
                created_at = meta.get("created_at", "Unknown")
                learning_source = meta.get("learning_source", learning_source)
            except Exception:
                pass

        return (
            f"=== PENDING LINKEDIN DRAFT ===\n"
            f"Status: Waiting for user confirmation\n"
            f"Learning Source: {learning_source}\n"
            f"Created At: {created_at}\n"
            f"Image Attached: {'Yes (' + str(image_file) + ')' if has_image else 'No'}\n\n"
            f"--- CAPTION ---\n{caption}\n"
        )
    except Exception as e:
        return f"Error reading pending draft: {_sanitize_error(e)}"


# ==============================================================================
# 7. MCP TOOL: cancel_pending_draft
# ==============================================================================
@mcp.tool()
def cancel_pending_draft() -> str:
    """
    Cancel and clear the current pending draft so it will not be published.
    Archives the draft to user_data/archive/<timestamp>_cancelled/.
    """
    caption_file = PENDING_POST_DIR / "caption.txt"
    image_file = PENDING_POST_DIR / "image.png"
    if not caption_file.exists() and not image_file.exists():
        return "No active pending draft to cancel."

    _archive_pending_draft(reason="cancelled")
    return "The pending draft has been cancelled and cleared."


# ==============================================================================
# PLAYWRIGHT MEDIA UPLOAD HELPER
# ==============================================================================
async def _handle_post_upload_dialogs(page) -> None:
    """
    Handle any intermediate media screens (e.g. LinkedIn's 'Next' button after image selection).
    """
    await page.wait_for_timeout(2000)
    next_selectors = [
        "button:has-text('Next')",
        "button[aria-label='Next']",
        "button.share-box-footer__primary-btn",
        "div.share-box-footer button.artdeco-button--primary",
        "button.media-editor-header__action",
    ]
    for n_sel in next_selectors:
        try:
            next_btn = page.locator(n_sel).first
            if await next_btn.count() > 0 and await next_btn.is_visible():
                logger.info(f"Found media editor Next button ({n_sel}), clicking to proceed...")
                await next_btn.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue


async def upload_image_to_composer(page, composer_container, image_path: Path) -> None:
    """
    Upload an image into the active LinkedIn composer using Playwright.
    Handles file chooser events, media selection dialogs, and 'Next' confirmation.
    Verifies that the image preview appears before proceeding.
    """
    if not image_path.exists() or image_path.stat().st_size == 0:
        raise RuntimeError(f"Image file not found or empty at {image_path}")

    logger.info(f"Attempting to upload image: {image_path}")

    # Check for direct file input first (often attached or hidden in composer)
    file_input = page.locator("input[type='file'][accept*='image'], input[type='file']").first
    uploaded_via_input = False
    if await file_input.count() > 0:
        try:
            await file_input.set_input_files(str(image_path))
            logger.info("Uploaded image via direct input[type='file']")
            uploaded_via_input = True
        except Exception as e:
            logger.debug(f"Direct set_input_files failed ({_sanitize_error(e)}), falling back to media button click")

    if not uploaded_via_input:
        # Locate Media / Photo button in composer container or on page
        media_button_locators = [
            composer_container.locator("button[aria-label='Media']"),
            composer_container.locator("button[aria-label='Add media']"),
            composer_container.locator("button[aria-label*='media' i]"),
            composer_container.locator("button:has(svg#image-medium)"),
            composer_container.locator("button:has-text('Media')"),
            composer_container.locator("button:has-text('Photo')"),
            page.locator("button[aria-label='Media']"),
            page.locator("button[aria-label='Add media']"),
            page.locator("button:has(svg#image-medium)"),
        ]

        media_btn = None
        for loc in media_button_locators:
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    media_btn = loc.first
                    break
            except Exception:
                continue

        if not media_btn:
            raise RuntimeError("Media button not found in LinkedIn composer.")

        logger.info("Found Media button in composer, clicking to open file chooser...")

        # Click media button and intercept the file chooser
        try:
            async with page.expect_file_chooser(timeout=8000) as fc_info:
                await media_btn.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(image_path))
            logger.info("Successfully set files via file chooser.")
        except PlaywrightTimeoutError:
            logger.info("File chooser not immediately triggered, checking for upload modal/input...")
            modal_input = page.locator("input[type='file']").first
            if await modal_input.count() > 0:
                await modal_input.set_input_files(str(image_path))
            else:
                select_btn = page.locator("button:has-text('Select files'), button:has-text('Upload from computer')").first
                if await select_btn.count() > 0 and await select_btn.is_visible():
                    async with page.expect_file_chooser(timeout=8000) as fc_info2:
                        await select_btn.click()
                    fc2 = await fc_info2.value
                    await fc2.set_files(str(image_path))
                else:
                    raise RuntimeError("Failed to trigger file chooser or find file input for media upload.")

    # Handle intermediate image editor/preview screen (e.g. 'Next' button)
    await _handle_post_upload_dialogs(page)

    # Verify that image preview is detected
    preview_selectors = [
        "div.media-editor, .media-editor-image, .image-cropper",
        "div.share-media-preview, div.share-box-media-preview",
        "div[role='dialog'] img",
        "img[src*='blob:']",
        "div.share-creation-state__preview",
    ]
    preview_found = False
    for _ in range(8):
        for psel in preview_selectors:
            try:
                elem = page.locator(psel).first
                if await elem.count() > 0 and await elem.is_visible():
                    preview_found = True
                    break
            except Exception:
                pass
        if preview_found:
            break
        await page.wait_for_timeout(1000)

    if not preview_found:
        any_media = page.locator(".share-creation-state div:has(img), [data-sdui-screen*='ShareCompose'] img").first
        if await any_media.count() > 0:
            preview_found = True

    if not preview_found:
        raise RuntimeError("Image preview not detected in composer after upload.")


# ==============================================================================
# 8. MCP TOOL: post_to_linkedin
# ==============================================================================
@mcp.tool()
async def post_to_linkedin(content: str = "", image_path: str = "") -> str:
    """
    Publish an approved post to LinkedIn using Playwright browser automation.
    If content is empty, publishes the active pending draft from user_data/pending_post/.
    Requires both caption and image. Aborts if image upload fails.
    Reuses a local persistent Chromium profile (./user_data) to maintain login.
    """
    # 1. Resolve content and image from arguments or pending draft
    target_content = (content or "").strip()
    target_image = (image_path or "").strip()

    caption_file = PENDING_POST_DIR / "caption.txt"
    pending_image_file = PENDING_POST_DIR / "image.png"

    is_publishing_pending_draft = not target_content

    if is_publishing_pending_draft:
        if not caption_file.exists():
            return (
                "Error: No post content provided and no pending draft found. "
                "No active pending draft found. "
                "Please create a draft first with 'Create today\\'s LinkedIn post'."
            )
        try:
            target_content = caption_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            return f"Error reading pending draft caption: {_sanitize_error(e)}"

        if not target_content:
            return "Error: Pending caption is empty. No active pending draft found."

        # Verify pending image exists
        if not pending_image_file.exists() or pending_image_file.stat().st_size == 0:
            return (
                "Error: Pending image is missing. Both caption and image are required to publish. "
                "Please regenerate the image or create a new draft."
            )
        final_image_path = pending_image_file.resolve()
    else:
        # Explicit content provided directly
        if target_image:
            candidate_path = Path(target_image).resolve()
            if candidate_path.exists() and candidate_path.is_file():
                final_image_path = candidate_path
            else:
                return f"Error: Specified image file not found at {target_image}"
        elif pending_image_file.exists() and pending_image_file.stat().st_size > 0:
            final_image_path = pending_image_file.resolve()
        else:
            final_image_path = None

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Launch persistent Playwright Chromium browser
    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(USER_DATA_DIR),
                headless=False,
                viewport={"width": 1280, "height": 800},
            )
        except Exception as e:
            return f"Error launching browser: {_sanitize_error(e)}"

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # 3. Open LinkedIn feed directly
            try:
                await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
            except PlaywrightTimeoutError:
                return "Error: Timed out opening LinkedIn feed. Please check your internet connection."

            # 4. Check if user is logged in
            async def check_logged_in() -> bool:
                if "/feed" in page.url:
                    return True
                try:
                    if await page.get_by_role("button", name=re.compile(r"start a post", re.I)).count() > 0:
                        return True
                except Exception:
                    pass
                try:
                    if await page.locator("nav.global-nav, #global-nav, div.global-nav__content").count() > 0:
                        return True
                except Exception:
                    pass
                return False

            if not await check_logged_in():
                login_timeout = 300  # 5 minutes
                poll_interval = 2
                elapsed = 0
                while elapsed < login_timeout:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    if await check_logged_in():
                        break
                else:
                    return (
                        "Error: LinkedIn login was not completed within 5 minutes. "
                        "Please run the tool again and sign in using your LinkedIn email and password "
                        "(avoid 'Sign in with Google' as Google blocks automated browsers)."
                    )

            if "/feed" not in page.url:
                try:
                    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass

            await page.wait_for_timeout(3000)

            # 5. Dismiss non-essential popups/overlays
            for dismiss_sel in [
                "button[aria-label='Dismiss']",
                "button[aria-label='Got it']",
                "button.artdeco-modal__dismiss",
                "button:has-text('Got it')",
                "button:has-text('Dismiss')",
                "button:has-text('Not now')",
            ]:
                try:
                    dismiss_btns = page.locator(dismiss_sel)
                    if await dismiss_btns.count() > 0 and await dismiss_btns.first.is_visible():
                        await dismiss_btns.first.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass

            try:
                collapse_btn = page.locator(
                    "button[data-control-name='overlay.collapse_conversation'], "
                    "button[aria-label*='Collapse messaging'], "
                    ".msg-overlay-bubble-header__control--close"
                )
                if await collapse_btn.count() > 0 and await collapse_btn.first.is_visible():
                    await collapse_btn.first.click()
                    await page.wait_for_timeout(500)
            except Exception:
                pass

            # 6. Locate and click "Start a post" button
            start_post_button = None
            start_post_locators = [
                page.get_by_role("button", name=re.compile(r"start a post", re.I)),
                page.locator("button.share-box-feed-entry__trigger"),
                page.locator(".share-box-feed-entry button"),
                page.locator("div.share-box-feed-entry__wrapper button"),
                page.get_by_text("Start a post", exact=False),
            ]
            for loc in start_post_locators:
                try:
                    if await loc.count() > 0 and await loc.first.is_visible():
                        start_post_button = loc.first
                        break
                except Exception:
                    continue

            if not start_post_button:
                try:
                    primary = page.get_by_role("button", name=re.compile(r"start a post", re.I))
                    await primary.first.wait_for(state="visible", timeout=15000)
                    start_post_button = primary.first
                except PlaywrightTimeoutError:
                    return "Error: Could not locate 'Start a post' button on LinkedIn feed."

            await start_post_button.scroll_into_view_if_needed()
            await start_post_button.click()

            # 7. Wait for composer dialog to fully appear
            dialog_locators = [
                page.locator("[data-sdui-screen*='ShareCompose']"),
                page.locator(".share-creation-state"),
                page.locator(".share-box-modal"),
                page.locator("div.feed-shared-modal"),
                page.locator(".ProseMirror"),
                page.locator("div[role='dialog']").filter(has=page.locator("[contenteditable='true'], .ProseMirror, button:has-text('Post')")),
            ]
            dialog = None
            for d_loc in dialog_locators:
                try:
                    await d_loc.first.wait_for(state="visible", timeout=12000)
                    dialog = d_loc.first
                    break
                except Exception:
                    continue

            await page.wait_for_timeout(1500)

            # 8. Detect the visible and editable post editor inside the composer
            editor = None
            editor_selectors = [
                ".ProseMirror",
                "[contenteditable='true']",
                "[role='textbox']",
                ".ql-editor",
                "div.editor-content",
                "div[data-placeholder]",
                "div[aria-multiline='true']",
                "p",
            ]

            if dialog:
                for sel in editor_selectors:
                    try:
                        candidates = dialog.locator(sel)
                        count = await candidates.count()
                        for i in range(count):
                            el = candidates.nth(i)
                            if await el.is_visible():
                                is_editable = await el.is_editable()
                                is_content_editable = await el.evaluate("node => node.isContentEditable")
                                if is_editable or is_content_editable:
                                    editor = el
                                    break
                        if editor:
                            break
                    except Exception:
                        continue

            if not editor:
                page_candidates = [
                    page.locator("[data-sdui-screen*='ShareCompose'] .ProseMirror"),
                    page.locator("[data-sdui-screen*='ShareCompose'] [contenteditable='true']"),
                    page.locator("div[role='dialog'] [contenteditable='true']"),
                    page.locator(".share-creation-state [contenteditable='true']"),
                    page.locator("div[role='dialog'] div[role='textbox']"),
                    page.locator("[contenteditable='true']:not(.msg-overlay-bubble-header):not(.msg-overlay-conversation-bubble [contenteditable='true'])"),
                    page.get_by_role("textbox", name=re.compile(r"what do you want to talk about", re.I)),
                ]
                for cand in page_candidates:
                    try:
                        if await cand.count() > 0 and await cand.first.is_visible():
                            is_editable = await cand.first.is_editable()
                            is_content_editable = await cand.first.evaluate("node => node.isContentEditable")
                            if is_editable or is_content_editable:
                                editor = cand.first
                                break
                    except Exception:
                        continue

            if not editor:
                return "Error: Could not locate post editor in composer dialog."

            # 9. Insert post content and verify it appears in the composer
            await editor.click()
            await page.wait_for_timeout(500)
            await page.keyboard.press("Control+A")
            await page.keyboard.insert_text(target_content)

            await page.keyboard.press("End")
            await page.keyboard.press("Space")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(1000)

            editor_text = (await editor.inner_text()).strip()
            if not editor_text or len(editor_text) < min(10, len(target_content)):
                try:
                    await editor.fill(target_content)
                except Exception:
                    await editor.click()
                    await page.keyboard.type(target_content, delay=15)
                await page.wait_for_timeout(1000)
                editor_text = (await editor.inner_text()).strip()

            if not editor_text:
                return "Error: Failed to enter post text into the LinkedIn editor. Text did not appear in composer."

            # Determine the active modal container that encloses our editor
            composer_container = None
            for container_sel in [
                "[data-sdui-screen*='ShareCompose']",
                "div.share-creation-state",
                "div.share-box-modal",
                "div.feed-shared-modal",
                "div.artdeco-modal",
                "div[role='dialog']",
            ]:
                try:
                    parent_modal = page.locator(container_sel).filter(has=editor).first
                    if await parent_modal.count() > 0:
                        composer_container = parent_modal
                        break
                except Exception:
                    pass

            if not composer_container:
                try:
                    sdui_modal = page.locator("[data-sdui-screen*='ShareCompose']").first
                    if await sdui_modal.count() > 0 and await sdui_modal.is_visible():
                        composer_container = sdui_modal
                except Exception:
                    pass

            container_scope = composer_container if composer_container else dialog

            # 10. If an image is attached, upload it to the composer
            if final_image_path:
                try:
                    logger.info(f"Uploading image to LinkedIn composer: {final_image_path}")
                    await upload_image_to_composer(page, container_scope, final_image_path)
                    await page.wait_for_timeout(2000)
                except Exception as img_err:
                    safe_img_err = _sanitize_error(img_err)
                    logger.error(f"Image upload failed: {safe_img_err}")
                    return f"Error uploading image to LinkedIn composer: {safe_img_err}"

            # Verify caption and image are both present in composer before proceeding to post
            final_editor_text = (await editor.inner_text()).strip()
            if not final_editor_text:
                return "Error: Post caption is missing or was cleared before publishing."

            if final_image_path:
                composer_has_image = False
                for img_sel in ["img", ".share-media-preview", "div[role='dialog'] img", "div.share-box-media-preview"]:
                    img_loc = container_scope.locator(img_sel).first
                    if await img_loc.count() > 0 and await img_loc.is_visible():
                        composer_has_image = True
                        break
                if not composer_has_image:
                    return "Error: Image preview not detected in composer before final post. Aborting to avoid publishing text-only post."

            # 11. Locate the final Post button using targeted selectors first, then fallback inspection
            post_button_info = None
            diagnostics_button_list = []

            targeted_locators = []
            if composer_container:
                targeted_locators.extend([
                    composer_container.get_by_role("button", name="Post", exact=True),
                    composer_container.locator("button").filter(has_text=re.compile(r"^\s*Post\s*$", re.I)),
                    composer_container.locator("button.share-actions__primary-action"),
                ])

            targeted_locators.extend([
                page.get_by_role("button", name="Post", exact=True),
                page.locator("[data-sdui-screen*='ShareCompose'] button").filter(has_text=re.compile(r"^\s*Post\s*$", re.I)),
                page.locator("button.share-actions__primary-action"),
                page.locator("button").filter(has_text=re.compile(r"^\s*Post\s*$", re.I)),
            ])

            for target in targeted_locators:
                try:
                    if await target.count() > 0 and await target.first.is_visible():
                        btn = target.first
                        text = (await btn.inner_text()).strip()
                        aria_label = (await btn.get_attribute("aria-label")) or ""
                        cls = (await btn.get_attribute("class")) or ""
                        is_disabled = await btn.is_disabled()
                        aria_disabled = (await btn.get_attribute("aria-disabled")) == "true"
                        post_button_info = {
                            "text": text,
                            "aria_label": aria_label,
                            "class": cls,
                            "disabled": is_disabled or aria_disabled,
                            "locator": btn,
                        }
                        logger.info(f"Targeted match found for Post button: text='{text}', disabled={is_disabled or aria_disabled}")
                        break
                except Exception:
                    continue

            # Fallback inspection
            if not post_button_info and container_scope:
                buttons_locator = container_scope.locator("button, [role='button']")
                btn_count = await buttons_locator.count()
                logger.info(f"Inspecting {btn_count} buttons inside container scope...")
                candidate_buttons = []

                for i in range(btn_count):
                    btn = buttons_locator.nth(i)
                    try:
                        if not await btn.is_visible():
                            continue
                        text = (await btn.inner_text()).strip()
                        aria_label = (await btn.get_attribute("aria-label")) or ""
                        cls = (await btn.get_attribute("class")) or ""
                        data_ctl = (await btn.get_attribute("data-control-name")) or ""
                        is_disabled = await btn.is_disabled()
                        aria_disabled = (await btn.get_attribute("aria-disabled")) == "true"
                        disabled = is_disabled or aria_disabled

                        btn_info = {
                            "index": i,
                            "text": text,
                            "aria_label": aria_label,
                            "class": cls,
                            "data_control": data_ctl,
                            "disabled": disabled,
                            "locator": btn,
                        }
                        diagnostics_button_list.append(
                            f"Button #{i}: text='{text}', aria-label='{aria_label}', disabled={disabled}, class='{cls[:50]}'"
                        )

                        text_lower = text.lower()
                        aria_lower = aria_label.lower()
                        is_post = False
                        if text_lower == "post" or aria_lower == "post":
                            is_post = True
                        elif "share-actions__primary-action" in cls or "primary-action" in cls:
                            is_post = True
                        elif "post" in text_lower and not any(w in text_lower for w in ["start", "who can see", "schedule", "visibility"]):
                            is_post = True
                        elif "post" in aria_lower and not any(w in aria_lower for w in ["start", "who can see", "schedule", "visibility"]):
                            is_post = True

                        if is_post:
                            candidate_buttons.append(btn_info)
                    except Exception:
                        continue

                if candidate_buttons:
                    for c in candidate_buttons:
                        if not c["disabled"]:
                            post_button_info = c
                            break
                    if not post_button_info:
                        post_button_info = candidate_buttons[-1]

            if not post_button_info:
                buttons_summary = "; ".join(diagnostics_button_list[:5])
                return f"Error: Could not locate the 'Post' button inside the active composer dialog. Visible buttons: [{buttons_summary}]."

            post_button = post_button_info["locator"]
            logger.info(
                f"Selected Post button: text='{post_button_info.get('text')}', "
                f"aria-label='{post_button_info.get('aria-label')}', "
                f"disabled={post_button_info.get('disabled')}, "
                f"class='{post_button_info.get('class', '')[:60]}'"
            )

            await post_button.scroll_into_view_if_needed()

            is_disabled = await post_button.is_disabled()
            aria_disabled = (await post_button.get_attribute("aria-disabled")) == "true"
            if is_disabled or aria_disabled:
                logger.info("Post button currently disabled, waiting for enablement...")
                try:
                    await page.wait_for_function(
                        "b => b && !b.disabled && b.getAttribute('aria-disabled') !== 'true'",
                        arg=await post_button.element_handle(),
                        timeout=8000,
                    )
                    is_disabled = await post_button.is_disabled()
                    aria_disabled = (await post_button.get_attribute("aria-disabled")) == "true"
                except Exception:
                    pass

            if is_disabled or aria_disabled:
                return "Error: The 'Post' button remained disabled. Content may not have been accepted by LinkedIn."

            # 12. Click the verified Post button
            logger.info("Clicking the Post button...")
            await post_button.click()

            # 13. Verify publishing completion
            dialog_closed = False
            try:
                if composer_container:
                    await composer_container.wait_for(state="hidden", timeout=25000)
                    dialog_closed = True
                elif editor:
                    await editor.wait_for(state="hidden", timeout=25000)
                    dialog_closed = True
                else:
                    await page.locator("[data-sdui-screen*='ShareCompose'], div.share-creation-state, .share-box-modal").first.wait_for(state="hidden", timeout=25000)
                    dialog_closed = True
            except PlaywrightTimeoutError:
                try:
                    if editor and not await editor.is_visible():
                        dialog_closed = True
                    elif post_button and not await post_button.is_visible():
                        dialog_closed = True
                except Exception:
                    pass

            if not dialog_closed:
                error_banner = page.locator("div.artdeco-inline-feedback--error, .feed-shared-error")
                if await error_banner.count() > 0 and await error_banner.first.is_visible():
                    err_msg = await error_banner.first.inner_text()
                    return f"Error from LinkedIn: {err_msg.strip()}"
                return "Error: Composer dialog did not close after clicking Post. The post may not have been published."

            await page.wait_for_timeout(2000)
            toast = page.locator("div.artdeco-toast-item, .feed-shared-toast")
            if await toast.count() > 0 and await toast.first.is_visible():
                toast_text = await toast.first.inner_text()
                logger.info(f"LinkedIn confirmation toast: {toast_text.strip()}")

            # Archive / clear the pending draft so it cannot be double-published
            _archive_pending_draft(reason="published")

            image_note = " with local technical visual" if final_image_path else ""
            return f"LinkedIn post{image_note} published successfully."

        except Exception as e:
            return f"Error publishing post: {_sanitize_error(e)}"
        finally:
            await context.close()


# ==============================================================================
# SERVER STARTUP (stdio)
# ==============================================================================
if __name__ == "__main__":
    mcp.run()
