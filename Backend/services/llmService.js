const axios = require("axios");

async function generateMarketingPrompt(prompt) {
    try {
        const response = await axios.post("http://localhost:8000/generate/", { message: prompt });
        return response.data.generated_text;
    } catch (error) {
        console.error("Error generating prompt:", error);
        return "Failed to generate prompt.";
    }
}

module.exports = { generateMarketingPrompt };
