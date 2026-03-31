# Reinforcement Learning for Agentic Orchestration

A research summary covering how reinforcement learning (RL) enhances multi-agent LLM systems, with focus on the orchestrator-calls-agents design pattern. This document surveys recent papers (mid-2025 through early 2026), categorizes the approaches, and identifies key design axes.

## The Core Question

Given an orchestrator agent calling *n* specialist agents, can RL improve the decision of which agent to call, when, and how the overall workflow evolves? The answer, based on recent literature, is a strong yes. Several distinct paradigms have emerged, each making different trade-offs on what gets trained, what gets searched, and where the simulation budget goes.

## Taxonomy of Approaches

Five broad categories describe how RL enters agentic orchestration:

| Category                          | Core Idea                                                                                   | Trains Weights?  | Needs Simulator?     | Key Papers                                    |
| --------------------------------- | ------------------------------------------------------------------------------------------- | ---------------- | -------------------- | --------------------------------------------- |
| RL-trained orchestrator           | A central "puppeteer" LLM trained via RL to sequence agents                                 | Yes              | Environment rollouts | Evolving Orchestration, Dr. MAS, Stronger-MAS |
| MCTS + preference optimization    | Tree search explores agent/action sequences; DPO/offline RL refines policy                  | Yes (offline)    | Simulated rollouts   | Agent Q, AFlow                                |
| Value-guided planning (no search) | Lightweight value function steers an LLM at inference time                                  | Value model only | Offline data         | Planning without Search                       |
| Process-reward RL                 | RL rewards intermediate steps (tool calls, agent invocations) rather than final answer only | Yes              | Environment rollouts | RLTR, Turn-Level Credit Assignment            |
| Multi-turn self-evolution         | Agents improve through multi-turn RL trajectories with environment feedback                 | Yes              | Environment rollouts | RAGEN / StarPO                                |

## RL-Trained Orchestrator: The Puppeteer Paradigm

The most direct answer to the original question. A dedicated orchestrator model is fine-tuned with RL to select, sequence, and coordinate specialist agents.

### Multi-Agent Collaboration via Evolving Orchestration (NeurIPS 2025)

A centralized orchestrator ("puppeteer") trained via RL dynamically directs specialist agents ("puppets") in response to evolving task states. Key contributions:

- Static multi-agent topologies (chain, debate, voting) fail as task complexity grows because coordination overhead scales poorly
- The orchestrator observes partial task state and selects the next agent to invoke, then observes that agent's output and repeats
- RL training (GRPO-style) lets the orchestrator learn adaptive routing policies that outperform any fixed topology
- The method generalizes across agent pool sizes without retraining

Reference: Dang et al. "Multi-Agent Collaboration via Evolving Orchestration." NeurIPS 2025. [OpenReview](https://openreview.net/forum?id=L0xZPXT3le)

### Stronger-MAS and AT-GRPO (ICLR 2026)

Identifies that standard GRPO breaks down in multi-agent settings because prompts differ by role and by turn, violating the grouped-sampling assumption. Proposes AT-GRPO (Agent- and Turn-wise GRPO):

- Groups trajectories by agent role and interaction turn for advantage normalization
- Supports both single-policy (shared backbone) and multi-policy (separate per-role) training
- On long-horizon planning, jumps from 14–47% (single-agent RL) to 96–99.5% accuracy
- Gains of 3.87–7.62% on coding, 9.0–17.93% on math over single-agent GRPO

Reference: Zhao et al. "Stronger-MAS: Multi-Agent Reinforcement Learning for Collaborative LLMs." ICLR 2026. [arXiv:2510.11062](https://arxiv.org/abs/2510.11062). Code: [PettingLLMs](https://github.com/pettingllms-ai/PettingLLMs)

### Dr. MAS: Stable Multi-Agent RL Training (Feb 2026)

Theoretically identifies that global GRPO advantage normalization causes gradient-norm explosion when agents have diverse reward distributions. Proposes agent-wise remedy:

- Each agent normalizes advantages using its own reward statistics, not a global baseline
- Supports heterogeneous agent-model assignments (co-training 7B and 3B models)
- +5.6% avg@16 on math, +15.2% avg@16 on search over vanilla GRPO
- Largely eliminates gradient spikes observed in prior multi-agent RL training

Reference: Feng et al. "Dr. MAS: Stable Reinforcement Learning for Multi-Agent LLM Systems." Feb 2026. Code: [DrMAS](https://github.com/langfengQ/DrMAS)

## MCTS + Preference Optimization: Search-Based Orchestration

Rather than training an orchestrator end-to-end with on-policy RL, these approaches use tree search to explore agent/action sequences, then distill the search results back into the model via offline RL or preference optimization.

### Agent Q (Aug 2024)

Combines Monte Carlo Tree Search (MCTS) with Direct Preference Optimization (DPO) for autonomous web agents:

- MCTS guides trajectory collection by exploring action branches at each step
- A self-critique mechanism evaluates trajectory quality without ground-truth labels
- Off-policy DPO iteratively refines the model using MCTS-collected preference pairs
- Achieves significant gains on WebShop and real-world web navigation tasks

This pattern directly applies to orchestrator design: treat each "which agent to call" decision as a search node, use MCTS to explore, and train the orchestrator via DPO on the collected preferences.

Reference: Putta et al. "Agent Q: Advanced Reasoning and Learning for Autonomous AI Agents." arXiv:2408.07199. Code: [agent-q](https://github.com/sentient-engineering/agent-q)

### AFlow: Automating Agentic Workflow Generation (ICLR 2025 Oral)

Reformulates workflow optimization as a search problem over code-represented workflows:

- Workflows are directed graphs where nodes are LLM-invoking steps connected by edges
- MCTS explores the space of possible workflow modifications (add/remove/rewire nodes)
- Tree-structured experience and execution feedback guide the search
- Surpasses manually constructed workflows on six reasoning datasets (HumanEval, GSM8K, MATH, HotpotQA, DROP, MBPP)

Directly relevant to the orchestrator question: AFlow automates constructing the orchestration policy itself, rather than hand-designing which agents get called in what order.

Reference: Zhang et al. "AFlow: Automating Agentic Workflow Generation." ICLR 2025. [arXiv:2410.10762](https://arxiv.org/abs/2410.10762). Code: [AFlow](https://github.com/FoundationAgents/AFlow)

## Value-Guided Planning Without Search

### Planning without Search (May 2025)

Key insight: instead of training an orchestrator policy or running expensive MCTS, learn a lightweight value function that guides an existing LLM at inference time:

- Goal-conditioned value functions model probabilities of various outcomes given an action
- Values operate at the level of high-level thoughts/strategies rather than low-level tokens
- A small auxiliary value model guides the base LLM via inference APIs, without fine-tuning the LLM
- Works with frontier API-based models (GPT-4, Claude) since no weight access is required

Three innovations over standard multi-turn RL:

1. RL over high-level strategies rather than environment actions
2. Goal-conditioned RL learning likelihoods of reaching goal states rather than scalar values
3. No LLM fine-tuning; a lightweight auxiliary value function steers search

For orchestrator design, this means: learn a value function that predicts "if I call Agent X next, what is the probability of reaching a successful outcome?" and use it to guide the orchestrator's decisions at inference time.

Reference: Hong et al. "Planning without Search: Refining Frontier LLMs with Offline Goal-Conditioned RL." arXiv:2505.18098

## Process-Reward RL: Rewarding the Orchestration Steps

Standard RL for agents rewards only the final outcome. Process-reward approaches assign credit to intermediate steps, which directly improves orchestrator decision quality.

### RLTR: Reinforcement Learning with Tool-use Rewards (EMNLP 2025)

Decouples agent training into a Planner (which decides tool/agent invocations) and a Summarizer (which produces final answers):

- The Planner is optimized with RL using tool-completeness rewards, not answer-correctness rewards
- This single-objective optimization avoids the conflict between "did you call the right tools?" and "is the final answer correct?"
- 8–12% improvement in planning performance over end-to-end RL
- Works on both 1.7B and 8B models

For orchestrator design, this decoupling is directly applicable: reward the orchestrator for calling the right agents in the right order, separately from the final task reward.

Reference: Li et al. "Encouraging Good Processes Without the Need for Good Answers: Reinforcement Learning for LLM Agent Planning." EMNLP 2025 Industry. [arXiv:2508.19598](https://arxiv.org/abs/2508.19598)

### Turn-Level Credit Assignment (NeurIPS 2025)

Extends GRPO and PPO to multi-turn variants with fine-grained turn-level rewards:

- Models multi-turn agent interaction as a Markov Decision Process
- Assigns reward at each turn (agent invocation) rather than only at episode end
- Achieves greater stability, faster convergence, and higher accuracy on reasoning-augmented search tasks
- Addresses the credit assignment problem: in a 10-turn orchestration, which agent call was the one that mattered?

Reference: Wei et al. "Reinforcing Multi-Turn Reasoning in LLM Agents via Turn-Level Reward Design and Credit Assignment." NeurIPS 2025.

### ReTool: Strategic Tool Use via RL (ICLR 2026)

Teaches LLMs when and how to invoke tools using RL with multi-turn real-time code execution:

- Dynamic interleaving of tool calls within reasoning chains
- RL policy rollouts include multi-turn real-time code execution in a sandboxed interpreter
- Learns to decide when a tool call adds value vs. when natural language reasoning suffices

Reference: Feng et al. "ReTool: Reinforcement Learning for Strategic Tool Use in LLMs." ICLR 2026. [arXiv:2504.11536](https://arxiv.org/abs/2504.11536)

## Multi-Turn Self-Evolution

### RAGEN / StarPO (Apr 2025)

Proposes StarPO (State-Thinking-Actions-Reward Policy Optimization), a trajectory-level RL framework for multi-turn agent training:

- Identifies the "Echo Trap" failure mode where reward variance collapses and gradients spike
- Addresses it with trajectory filtering, critic incorporation, and gradient stabilization (StarPO-S)
- Key findings for orchestrator training: diverse initial states, medium interaction granularity, and frequent sampling improve RL rollout quality
- Without reasoning-aware reward signals, agent reasoning fails to emerge from multi-turn RL

Reference: Wang et al. "RAGEN: Understanding Self-Evolution in LLM Agents via Multi-Turn Reinforcement Learning." arXiv:2504.20073. Code: [RAGEN](https://github.com/RAGEN-AI/RAGEN)

## World Models and Simulation

For physical-world orchestration (robotics, embodied agents), world models provide the simulation substrate that RL training requires.

### GenRL: Multimodal Foundation World Models (2024)

Connects vision-language model representations with generative world models for RL:

- Learns environment dynamics in a compact latent space, then trains policies in imagination
- Generalizes to new tasks in a data-free manner (no task-specific reward function required)
- Task specification via vision and/or language prompts, grounded through the world model

Relevant to orchestration in physical domains: an orchestrator can evaluate different agent-calling strategies by rolling them out in a learned world model before executing in the real environment.

Reference: Mazzaglia et al. "Multimodal Foundation World Models for Generalist Embodied Agents." arXiv. Project: [genrl](https://mazpie.github.io/genrl)

### LeCun's AMI Architecture

Lays out the theoretical paradigm: a World Model rolls the world forward under candidate actions, a cost module evaluates each hypothetical future, and an MPC-style planner executes only the first step of the minimum-cost plan before re-planning. Joint-embedding predictive architectures (JEPAs) forecast in abstract latent space rather than pixel space, reducing compute cost.

Reference: LeCun. "A Path Towards Autonomous Machine Intelligence." 2022.

## Design Decision Matrix

When applying RL to an orchestrator-calls-agents architecture, these axes determine the approach:

| Decision Axis                | Options                                                       | Trade-off                                                                        |
| ---------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| What gets trained?           | Orchestrator only / All agents jointly / Value function only  | Joint training is more powerful but harder to stabilize (Dr. MAS, Stronger-MAS)  |
| On-policy vs. off-policy RL? | GRPO/PPO (on-policy) vs. DPO/offline RL                       | On-policy is stronger but requires environment rollouts; offline works from logs |
| Search at inference time?    | None / MCTS / value-guided                                    | MCTS is expensive but thorough; value-guided is cheap and composable             |
| Reward granularity?          | Final outcome only / per-turn / per-tool-call                 | Finer granularity improves credit assignment but requires reward engineering     |
| Simulation substrate?        | Real environment / LLM-as-judge / learned world model         | World models enable cheap rollouts but introduce model error                     |
| Agent heterogeneity?         | Shared policy / role-specialized policies / mixed model sizes | Heterogeneous setups (3B + 7B) need per-agent normalization (Dr. MAS)            |

## Practical Recommendations

For an orchestrator-calls-agents system:

1. Start with AFlow-style MCTS to discover effective workflow structures from scratch, before committing to a fixed topology
2. Apply RLTR-style decoupled training: reward the orchestrator for routing quality independently from final task accuracy
3. Use AT-GRPO or Dr. MAS-style agent-wise normalization when co-training the orchestrator with multiple specialist agents
4. Add a value function (Planning without Search) for inference-time steering when the orchestrator LLM cannot be fine-tuned (e.g., API-only models)
5. Use turn-level credit assignment to attribute success/failure to specific agent invocations in multi-step workflows

## Comprehensive Surveys

| Survey                                                              | Date                 | Scope                                                                                                                                               |
| ------------------------------------------------------------------- | -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| The Landscape of Agentic RL for LLMs                                | Jan 2026 (104 pages) | Full coverage of agentic RL: planning, tool use, memory, reasoning, multi-agent, environments. [arXiv:2509.02547](https://arxiv.org/abs/2509.02547) |
| LLM-based Multi-Agent Systems: Workflow, Infrastructure, Challenges | 2024                 | Workflow design, infrastructure, coordination. [Springer](https://link.springer.com/article/10.1007/s44336-024-00009-2)                             |
| Multi-AI Agent Collaboration                                        | 2025                 | Theory and technology of multi-agent collaboration including deep RL. [ACM](https://dl.acm.org/doi/full/10.1145/3745238.3745531)                    |

## Curated Paper Lists

- Sebastian Raschka: [The State of RL for LLM Reasoning](https://magazine.sebastianraschka.com/p/the-state-of-llm-reasoning-model-training) (Apr 2025) and [LLM Research Papers 2025 (Jul–Dec)](https://magazine.sebastianraschka.com/p/llm-research-papers-2025-part2)
- [AGI-Edgerunners/LLM-Agents-Papers](https://github.com/AGI-Edgerunners/LLM-Agents-Papers) (updated Jul 2025)
- [luo-junyu/Awesome-Agent-Papers](https://github.com/luo-junyu/Awesome-Agent-Papers)
