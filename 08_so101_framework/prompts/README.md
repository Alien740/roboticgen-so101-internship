# Using the framework with ChatGPT or a local model

The rules and procedures are identical for every assistant; they come from [`../AGENTS.md`](../AGENTS.md).

## ChatGPT (web) or any chat-only model
The model cannot touch your PC, so it guides you and you type the commands.

1. Paste [`system_prompt.md`](system_prompt.md) into *Custom instructions* (or a Project's instructions),
   or as the first message of the chat.
2. Optionally attach `AGENTS.md`, `docs/PROCEDURES.md` and `docs/TROUBLESHOOTING.md` as files.
3. Paste one of the task prompts from [`tasks/`](tasks/) (setup, calibration, basket trials, drawing,
   troubleshooting) and follow along; paste the command output back after each step.

## A local model (Ollama, LM Studio, llama.cpp server, vLLM, ...)
Use a model that supports tool or function calling (for example Qwen 2.5 7B+, Llama 3.1 8B+, Mistral Nemo).

```
python prompts/local_agent.py --base-url http://localhost:11434/v1 --model qwen2.5:14b
# LM Studio: --base-url http://localhost:1234/v1     llama.cpp: --base-url http://localhost:8080/v1
```

`local_agent.py` connects the model to the tools in [`tools.json`](tools.json) (read-only commands,
`agent start/wait/answer/status/stop`, reading the docs). Every command that can **move the arm** needs
you to type `y` in the terminal first, regardless of what the model decides. Type your messages at the
`you>` prompt; `quit` exits.

## Any other agent framework
Give it `system_prompt.md` as the system prompt and the functions in `tools.json`. Each function maps
1:1 to a shell command (see `local_agent.py: run_tool`), so it can also be implemented as plain shell calls.
