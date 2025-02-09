from fastapi import FastAPI
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

app = FastAPI()

# Model and tokenizer setup
model_id = "marketeam/LLaMarketing"
tokenizer_id = "meta-llama/Meta-Llama-3-8B"
token = "hf_token"
device = "cuda" if torch.cuda.is_available() else "cpu"

tokenizer = AutoTokenizer.from_pretrained(tokenizer_id, token=token)
model = AutoModelForCausalLM.from_pretrained(
    model_id, torch_dtype=torch.bfloat16, token=token).to(device)

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

@app.post("/marketing/generate")
def generate_marketing_prompt(prompt: str, target_audience: str, product_features: str):
    input_text = (f"Generate a catchy marketing slogan for our product. "
                  f"Prompt: {prompt}. Target Audience: {target_audience}. "
                  f"Product Features: {product_features}.")
    
    # Tokenize input text
    inputs = tokenizer(input_text, return_tensors="pt").to(device)
    
    # Add logging to check tokenized inputs
    print(f"Inputs: {inputs}")
    
    # Generate response from the model (with a max_length to limit output size)
    outputs = model.generate(**inputs, max_length=10)  # Limit the response length to 100 tokens
    
    # Decode the generated text
    generated_text = tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]
    
    # Log the generated text
    print(f"Generated Text: {generated_text}")
    
    return {"response": generated_text}
