"""
Automated unit and integration tests for LinkedIn Learning Publisher MCP server (V2):
1. OpenAI dependency and configuration removal verification.
2. SVG generation (build_technical_svg generates valid XML/SVG for technical topics).
3. SVG to PNG conversion (convert_svg_to_png produces valid PNG files locally).
4. Image file creation (generate_linkedin_image saves to generated_images/ and pending_post/).
5. Pending draft creation (save_pending_draft saves caption, image, metadata with learning source).
6. Pending draft retrieval (get_pending_draft returns formatted details).
7. Image regeneration preserves caption (keeps existing caption.txt intact).
8. Post regeneration (replaces both caption and image in pending draft).
9. Draft cancellation (cancel_pending_draft archives to _cancelled and clears active draft).
10. Missing image protection (never publish without image).
11. Publish confirmation protection (drafts are not auto-published).
12. Resource and prompt primitives registered on MCP server.
13. Today's learning retrieval (reads daily_notes.txt, handles missing and empty notes).
14. Image upload validation (validates file existence before upload).
15. Successful publishing archives draft (draft moved to _published archive).
16. Duplicate publishing prevented (cannot double publish archived drafts).
"""

import asyncio
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from server import (
    DRAFT_ARCHIVE_DIR,
    GENERATED_IMAGES_DIR,
    PENDING_POST_DIR,
    _archive_pending_draft,
    build_technical_svg,
    cancel_pending_draft,
    convert_svg_to_png,
    format_linkedin_post,
    generate_linkedin_image,
    get_pending_draft,
    get_today_learning,
    mcp,
    post_to_linkedin,
    save_pending_draft,
    upload_image_to_composer,
)


class TestLinkedInPublisherV2(unittest.TestCase):

    def setUp(self):
        # Clean any existing pending post, generated images, and archive directories before test
        if PENDING_POST_DIR.exists():
            shutil.rmtree(PENDING_POST_DIR)
        if DRAFT_ARCHIVE_DIR.exists():
            shutil.rmtree(DRAFT_ARCHIVE_DIR)
        if GENERATED_IMAGES_DIR.exists():
            shutil.rmtree(GENERATED_IMAGES_DIR)

    def tearDown(self):
        # Clean test directories after test
        if PENDING_POST_DIR.exists():
            shutil.rmtree(PENDING_POST_DIR)
        if DRAFT_ARCHIVE_DIR.exists():
            shutil.rmtree(DRAFT_ARCHIVE_DIR)
        if GENERATED_IMAGES_DIR.exists():
            shutil.rmtree(GENERATED_IMAGES_DIR)

    # --------------------------------------------------------------------------
    # 1. OpenAI Dependency & Config Removal Verification
    # --------------------------------------------------------------------------
    def test_openai_dependency_and_config_removed(self):
        # Verify openai package is not imported in sys.modules by server
        self.assertNotIn("openai", sys.modules)

        # Verify no OPENAI environment variables are required
        with patch.dict("os.environ", {}, clear=True):
            # generate_linkedin_image should not raise or demand OPENAI_API_KEY
            async def _check():
                with patch("server.convert_svg_to_png") as mock_convert:
                    async def _fake_convert(svg_str, out_path):
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_bytes(b"dummy png")
                        return out_path

                    mock_convert.side_effect = _fake_convert
                    result = await generate_linkedin_image(image_prompt="RAG Pipeline")
                    self.assertNotIn("OPENAI_API_KEY", result)
                    self.assertIn("Technical diagram generated successfully", result)

            asyncio.run(_check())

    # --------------------------------------------------------------------------
    # 2. SVG Generation
    # --------------------------------------------------------------------------
    def test_svg_generation(self):
        # Test known RAG topic SVG generation
        svg_rag = build_technical_svg("RAG architecture")
        self.assertIn("<svg", svg_rag)
        self.assertIn("</svg>", svg_rag)
        self.assertIn("Retrieval-Augmented Generation", svg_rag)
        self.assertIn("Retriever", svg_rag)
        self.assertIn("LLM", svg_rag)

        # Test workflow arrow-separated steps
        svg_arrow = build_technical_svg("Data Ingest -> Transform -> Vector Index -> Query")
        self.assertIn("<svg", svg_arrow)
        self.assertIn("Vector Index", svg_arrow)

        # Test custom SVG passthrough
        custom_raw = '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"><rect fill="#000"/></svg>'
        svg_custom = build_technical_svg(custom_raw)
        self.assertEqual(svg_custom, custom_raw)

    # --------------------------------------------------------------------------
    # 3. SVG to PNG Conversion (Local rendering)
    # --------------------------------------------------------------------------
    def test_svg_to_png_conversion(self):
        output_file = GENERATED_IMAGES_DIR / "test_convert.png"
        test_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300">'
            '<rect width="600" height="300" fill="#0f172a"/>'
            '<text x="300" y="150" fill="white" font-size="20" text-anchor="middle">Test Render</text>'
            "</svg>"
        )

        async def _run():
            res_path = await convert_svg_to_png(test_svg, output_file, width=600, height=300)
            self.assertTrue(res_path.exists())
            self.assertGreater(res_path.stat().st_size, 0)

        asyncio.run(_run())

    # --------------------------------------------------------------------------
    # 4. Image File Creation
    # --------------------------------------------------------------------------
    def test_image_file_creation(self):
        async def _run():
            with patch("server.convert_svg_to_png") as mock_convert:
                async def _fake_convert(svg_str, out_path):
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(b"dummy png bytes")
                    return out_path

                mock_convert.side_effect = _fake_convert
                res = await generate_linkedin_image(image_prompt="MCP Architecture")
                self.assertIn("Technical diagram generated successfully", res)

                # Verify files exist in both generated_images/ and pending_post/
                pending_img = PENDING_POST_DIR / "image.png"
                self.assertTrue(pending_img.exists())
                self.assertGreater(pending_img.stat().st_size, 0)

                gen_images = list(GENERATED_IMAGES_DIR.glob("*.png"))
                self.assertGreater(len(gen_images), 0)

                # Verify metadata.json was written
                meta_file = PENDING_POST_DIR / "metadata.json"
                self.assertTrue(meta_file.exists())
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                self.assertTrue(meta.get("has_image"))
                self.assertEqual(meta.get("image_type"), "local_svg_png")

        asyncio.run(_run())

    # --------------------------------------------------------------------------
    # 5. Pending Draft Creation
    # --------------------------------------------------------------------------
    def test_pending_draft_creation(self):
        sample_caption = "Today I learned about RAG architecture! #LearningInPublic #AI"
        dummy_img = PENDING_POST_DIR / "temp_img.png"
        PENDING_POST_DIR.mkdir(parents=True, exist_ok=True)
        dummy_img.write_bytes(b"dummy image bytes")

        save_res = save_pending_draft(
            caption=sample_caption,
            image_path=str(dummy_img),
            learning_source="learnings://today/raw (daily_notes.txt)",
        )
        self.assertIn("Pending draft saved successfully", save_res)
        self.assertIn("Pending user confirmation", save_res)

        self.assertTrue((PENDING_POST_DIR / "caption.txt").exists())
        self.assertTrue((PENDING_POST_DIR / "image.png").exists())
        self.assertTrue((PENDING_POST_DIR / "metadata.json").exists())

        meta = json.loads((PENDING_POST_DIR / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "pending")
        self.assertFalse(meta.get("published", False))
        self.assertIn("daily_notes.txt", meta.get("learning_source", ""))

    # --------------------------------------------------------------------------
    # 6. Pending Draft Retrieval
    # --------------------------------------------------------------------------
    def test_pending_draft_retrieval(self):
        # Empty draft retrieval
        empty_res = get_pending_draft()
        self.assertIn("No pending draft", empty_res)

        # Saved draft retrieval
        sample_caption = "Today I learned about Transformers and Self-Attention!"
        dummy_img = PENDING_POST_DIR / "image.png"
        PENDING_POST_DIR.mkdir(parents=True, exist_ok=True)
        dummy_img.write_bytes(b"dummy image bytes")

        save_pending_draft(
            caption=sample_caption,
            image_path=str(dummy_img),
            learning_source="learnings://today/raw",
        )

        draft_details = get_pending_draft()
        self.assertIn(sample_caption, draft_details)
        self.assertIn("Waiting for user confirmation", draft_details)
        self.assertIn("Learning Source: learnings://today/raw", draft_details)
        self.assertIn("Image Attached: Yes", draft_details)

    # --------------------------------------------------------------------------
    # 7. Image Regeneration Preserves Caption
    # --------------------------------------------------------------------------
    def test_image_regeneration_preserves_caption(self):
        initial_caption = "Initial student learning caption on RAG architecture."
        save_pending_draft(caption=initial_caption)
        self.assertEqual((PENDING_POST_DIR / "caption.txt").read_text(encoding="utf-8").strip(), initial_caption)

        # Mock image generation to simulate "Regenerate the image"
        async def _run():
            with patch("server.convert_svg_to_png") as mock_convert:
                async def _fake_convert(svg_str, out_path):
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(b"new image bytes")
                    return out_path

                mock_convert.side_effect = _fake_convert
                await generate_linkedin_image("Refined RAG visual diagram")

            # Caption remains preserved
            self.assertTrue((PENDING_POST_DIR / "caption.txt").exists())
            self.assertEqual((PENDING_POST_DIR / "caption.txt").read_text(encoding="utf-8").strip(), initial_caption)

            # save_pending_draft with empty caption also preserves existing caption
            save_pending_draft(caption="")
            self.assertEqual((PENDING_POST_DIR / "caption.txt").read_text(encoding="utf-8").strip(), initial_caption)

        asyncio.run(_run())

    # --------------------------------------------------------------------------
    # 8. Post Regeneration
    # --------------------------------------------------------------------------
    def test_post_regeneration(self):
        # Initial draft
        save_pending_draft(caption="First caption")
        img1 = PENDING_POST_DIR / "image.png"
        img1.write_bytes(b"initial image")

        # Regenerate complete post
        new_caption = "Completely regenerated caption about LoRA fine-tuning"
        new_dummy_img = PENDING_POST_DIR / "temp_new_img.png"
        new_dummy_img.write_bytes(b"new image bytes")
        save_pending_draft(caption=new_caption, image_path=str(new_dummy_img))

        # Verify replacement
        self.assertEqual((PENDING_POST_DIR / "caption.txt").read_text(encoding="utf-8").strip(), new_caption)
        self.assertEqual((PENDING_POST_DIR / "image.png").read_bytes(), b"new image bytes")

    # --------------------------------------------------------------------------
    # 9. Draft Cancellation
    # --------------------------------------------------------------------------
    def test_draft_cancellation(self):
        save_pending_draft(caption="Draft to be cancelled")
        self.assertTrue((PENDING_POST_DIR / "caption.txt").exists())

        cancel_res = cancel_pending_draft()
        self.assertIn("cancelled and cleared", cancel_res)
        self.assertFalse((PENDING_POST_DIR / "caption.txt").exists())
        self.assertIn("No pending draft", get_pending_draft())

        cancelled_dirs = [d for d in DRAFT_ARCHIVE_DIR.iterdir() if d.is_dir() and "cancelled" in d.name]
        self.assertGreater(len(cancelled_dirs), 0)

    # --------------------------------------------------------------------------
    # 10. Missing Image Protection (Never publish without image)
    # --------------------------------------------------------------------------
    def test_missing_image_protection(self):
        save_pending_draft(caption="Caption without image")
        if (PENDING_POST_DIR / "image.png").exists():
            (PENDING_POST_DIR / "image.png").unlink()

        async def _test():
            res = await post_to_linkedin(content="")
            self.assertIn("Pending image is missing", res)
            self.assertIn("Both caption and image are required", res)
            # Ensure draft is NOT archived since it failed before publishing
            self.assertTrue((PENDING_POST_DIR / "caption.txt").exists())

        asyncio.run(_test())

    # --------------------------------------------------------------------------
    # 11. Publish Confirmation Protection (Draft not auto-published)
    # --------------------------------------------------------------------------
    def test_publish_confirmation_protection(self):
        save_pending_draft(caption="Draft awaiting explicit user confirmation")
        meta_file = PENDING_POST_DIR / "metadata.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "pending")
        self.assertFalse(meta.get("published", False))

        # Ensure no publish archive exists
        if DRAFT_ARCHIVE_DIR.exists():
            published_archives = [d for d in DRAFT_ARCHIVE_DIR.iterdir() if d.is_dir() and "published" in d.name]
            self.assertEqual(len(published_archives), 0)

    # --------------------------------------------------------------------------
    # 12. Resource and Prompt Primitives
    # --------------------------------------------------------------------------
    def test_resource_and_prompt_primitives(self):
        async def _check():
            tools = await mcp.list_tools()
            prompts = await mcp.list_prompts()
            resources = await mcp.list_resources()

            tool_names = [t.name for t in tools]
            prompt_names = [p.name for p in prompts]
            resource_uris = [str(r.uri) for r in resources]

            # Verify core tools
            self.assertIn("post_to_linkedin", tool_names)
            self.assertIn("generate_linkedin_image", tool_names)
            self.assertIn("save_pending_draft", tool_names)
            self.assertIn("get_pending_draft", tool_names)
            self.assertIn("cancel_pending_draft", tool_names)

            # Verify prompt
            self.assertIn("format_linkedin_post", prompt_names)

            # Verify resources
            self.assertTrue(any("learnings://today/raw" in u for u in resource_uris))
            self.assertTrue(any("draft://pending" in u for u in resource_uris))

        asyncio.run(_check())

        # Verify prompt instructions
        prompt_text = format_linkedin_post()
        self.assertIn("Workflow", prompt_text)
        self.assertIn("generate_linkedin_image", prompt_text)
        self.assertIn("save_pending_draft", prompt_text)
        self.assertIn("Publish it", prompt_text)
        self.assertIn("STRICT NEGATIVE CONSTRAINTS", prompt_text)

    # --------------------------------------------------------------------------
    # 13. Today's Learning Retrieval
    # --------------------------------------------------------------------------
    def test_today_learning_retrieval(self):
        # 1. Normal learning retrieval
        content = get_today_learning()
        self.assertIsInstance(content, str)
        self.assertGreater(len(content), 0)

        # 2. Missing daily_notes.txt
        with patch("server.DAILY_NOTES_FILE", Path("non_existent_file_notes.txt")):
            res_missing = get_today_learning()
            self.assertIn("Error: daily_notes.txt is missing", res_missing)

        # 3. Empty daily_notes.txt
        dummy_empty = PENDING_POST_DIR / "empty_notes.txt"
        PENDING_POST_DIR.mkdir(parents=True, exist_ok=True)
        dummy_empty.write_text("   \n  ", encoding="utf-8")
        with patch("server.DAILY_NOTES_FILE", dummy_empty):
            res_empty = get_today_learning()
            self.assertIn("Error: Daily learning in daily_notes.txt is empty", res_empty)

    # --------------------------------------------------------------------------
    # 14. Image Upload Validation
    # --------------------------------------------------------------------------
    def test_image_upload_validation(self):
        async def _test_upload():
            fake_path = Path("non_existent_image_12345.png")
            with self.assertRaises(RuntimeError) as ctx:
                await upload_image_to_composer(MagicMock(), MagicMock(), fake_path)
            self.assertIn("not found or empty", str(ctx.exception))

        asyncio.run(_test_upload())

    # --------------------------------------------------------------------------
    # 15. Successful Publishing Archives Draft
    # --------------------------------------------------------------------------
    def test_successful_publishing_archives_draft(self):
        save_pending_draft(caption="Ready to publish")
        dummy_img = PENDING_POST_DIR / "image.png"
        dummy_img.write_bytes(b"test image")

        _archive_pending_draft(reason="published")

        # Active pending_post files must be cleared
        self.assertFalse((PENDING_POST_DIR / "caption.txt").exists())
        self.assertFalse((PENDING_POST_DIR / "image.png").exists())

        published_dirs = [d for d in DRAFT_ARCHIVE_DIR.iterdir() if d.is_dir() and "published" in d.name]
        self.assertGreater(len(published_dirs), 0)
        archived_meta_file = published_dirs[-1] / "metadata.json"
        self.assertTrue(archived_meta_file.exists())
        archived_meta = json.loads(archived_meta_file.read_text(encoding="utf-8"))
        self.assertEqual(archived_meta.get("status"), "published")

    # --------------------------------------------------------------------------
    # 16. Duplicate Publishing Prevented
    # --------------------------------------------------------------------------
    def test_duplicate_publishing_prevented(self):
        save_pending_draft(caption="Already published post")
        _archive_pending_draft(reason="published")

        async def _run():
            res = await post_to_linkedin(content="")
            self.assertIn("No active pending draft found", res)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
