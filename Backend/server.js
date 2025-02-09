const express = require("express");
const cors = require("cors");
const marketingRoutes = require("./routes/marketing");

const app = express();
app.use(express.json());
app.use(cors());

app.use("/marketing", marketingRoutes);

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Server running on port ${PORT}`));
