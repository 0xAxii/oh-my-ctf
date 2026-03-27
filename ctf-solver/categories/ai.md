# AI — AI/ML Security

## Tools
python3, PyTorch, TensorFlow, numpy, scipy, Pillow, transformers, openai SDK

## Workflow
1. Identify — model type, API access, challenge goal (adversarial, prompt injection, model extraction, etc)
2. Analyze — probe the model/API for behavior patterns
3. Attack — craft adversarial input or exploit model weakness
4. Flag — extract from model output or side channel

## Common Types
### Prompt Injection
- Direct injection: override system prompt
- Indirect injection: inject via retrieved content
- Jailbreak: bypass safety filters
- Extraction: extract system prompt or training data

### Adversarial ML
- Image adversarial: FGSM, PGD, C&W attack
- Text adversarial: token manipulation, homoglyph
- Model evasion: fool classifier with crafted input

### Model Extraction
- Query-based: reconstruct model via API queries
- Side channel: timing, confidence scores
- Membership inference

### Data Poisoning
- Backdoor triggers in training data
- Label flipping attacks

## Key Rules
- Start with probing: send simple inputs, observe patterns
- For prompt injection: try encoding tricks (base64, rot13, language switching)
- For adversarial: use gradient-based methods if model weights available
- Write findings to findings_raw.md as you discover them
