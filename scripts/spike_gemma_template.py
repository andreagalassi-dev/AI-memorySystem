from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("mlx-community/gemma-4-26b-a4b-it-4bit")
messages = [{"role": "system", "content": "Sei un assistente."}, {"role": "user", "content": "Ciao"}]
prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
print("PROMPT:", repr(prompt))
