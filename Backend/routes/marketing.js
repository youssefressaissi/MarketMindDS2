const express = require("express");
const router = express.Router();
const { generateMarketingPrompt } = require("../services/llmService");

router.post("/generate", async (req, res) => {
    const { prompt } = req.body;
    if (!prompt) return res.status(400).json({ error: "Prompt is required" });

    const generatedText = await generateMarketingPrompt(prompt);
    res.json({ result: generatedText });
});

module.exports = router;
