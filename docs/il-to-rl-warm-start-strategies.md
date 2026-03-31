# Warm-Starting RL Policies from Imitation Learning: Strategies for the KUKA Hackathon

Practical guidance for using IL-pretrained policies (ACT, OpenPI) to warm-start RL training in an Isaac environment. This report synthesizes current literature, identifies key design decisions, and recommends concrete strategies.

## 📋 Key Questions to Resolve Before the Hackathon

Before selecting a strategy, the team needs answers to these questions.

### Task and Environment Scope

| Question                                                               | Why It Matters                                                                                                              |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| What is the Isaac environment task? (pick-place, insertion, assembly?) | Task horizon and reward sparsity dictate which warm-start method works best                                                 |
| What is the observation space? (state-based, image-based, or mixed?)   | Image-based policies (ACT, OpenPI) require special handling for RL critic training                                          |
| What is the action space dimensionality and parameterization?          | Action-chunked IL policies (ACT uses chunks of 100 steps) create intractably high-dimensional action spaces for standard RL |
| Is the reward dense or sparse?                                         | Sparse rewards amplify the need for warm-starting; dense rewards reduce it                                                  |

### Data and IL Policy Characteristics

| Question                                                                                 | Why It Matters                                                                                                             |
| ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| How many demonstration episodes are available?                                           | Fewer demos mean weaker IL policy, which affects warm-start quality (WSRL and IBRL both degrade when IL pretraining fails) |
| What is the IL policy architecture? (ACT = CVAE + Transformer, OpenPI = Diffusion-based) | Architecture determines which RL fine-tuning methods are compatible                                                        |
| Does the IL policy use action chunking?                                                  | Action chunking is critical: RL methods that operate per-timestep cannot directly fine-tune a chunk-based policy           |
| What is the pretrained IL policy success rate?                                           | A near-zero success rate IL policy provides no warm-start value; a 50%+ policy can meaningfully guide RL exploration       |

### RL Algorithm Selection

| Question                                         | Why It Matters                                                                                                                            |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| On-policy (PPO) vs off-policy (SAC)?             | On-policy methods are simpler to parallelize in Isaac Lab but less sample-efficient; off-policy methods benefit more from warm-start data |
| What RL framework is available in the Isaac env? | Isaac Lab natively supports `rsl_rl` (PPO) and `rl_games`; SAC-based methods require custom integration                                   |
| What is the GPU compute budget?                  | High update-to-data (UTD) ratio approaches like WSRL require significant compute per environment step                                     |

### Integration Architecture

| Question                                            | Why It Matters                                                                                                                              |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Should the IL and RL policies share weights?        | Weight sharing enables direct fine-tuning but risks catastrophic forgetting; separate networks are safer but don't transfer representations |
| Can the IL policy run inference during RL rollouts? | Methods like IBRL and JSRL require the IL policy to provide actions during RL training                                                      |
| Is Isaac Sim running headlessly with parallel envs? | Parallel envs make on-policy methods viable but complicate off-policy replay buffer strategies                                              |

## 🔬 Strategy 1: Warm-Start RL (WSRL)

The primary reference paper. Core insight: offline data retention is unnecessary if you properly manage the transition from IL to RL.

### How It Works

1. Pretrain policy and Q-function using offline RL (CalQL/CQL/IQL) on demonstration data
2. Run a warmup phase: collect ~5,000 environment steps using the frozen pretrained policy
3. Discard all offline data; fine-tune using standard online RL (SAC with high UTD) on only the warmup + newly collected data

### Key Findings

| Finding                                                                                                 | Implication                                                                               |
| ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Q-value "downward spiral" destroys learning when offline data is dropped abruptly                       | Warmup phase bridges the distribution gap between offline and online data                 |
| Retaining offline data during fine-tuning hurts asymptotic performance                                  | Pessimistic offline RL objectives (CQL regularizer) actively slow down online improvement |
| Non-pessimistic online RL (SAC) outperforms continuing with offline RL algorithms during fine-tuning    | Switch algorithms at the online boundary                                                  |
| Both policy and Q-function initialization matter, especially when offline data has broad coverage       | Initialize both actor and critic from pretraining                                         |
| WSRL achieves 20/20 success on real Franka peg insertion in 18 minutes total (11 min warmup + 7 min RL) | Practical for real-world applications                                                     |

### Relevance to the Hackathon

WSRL assumes the pretraining happens via offline RL (CalQL), not pure IL (ACT/OpenPI). For the KUKA hackathon, the team would need to either:

- Train an offline RL critic on the demonstration data first (using CalQL or IQL), then apply WSRL
- Use the IL policy only for warmup rollouts and train both actor and critic from scratch during online RL (losing the Q-function initialization benefit)

**Recommended adaptation:** Train CalQL on the recorded episodes to get both a policy and Q-function initialization, then apply WSRL as described.

### Reference

> Zhou, Peng, Li, Levine, Kumar. "Efficient Online Reinforcement Learning Fine-Tuning Need Not Retain Offline Data." arXiv:2412.07762, 2024.
> Code: https://github.com/zhouzypaul/wsrl

## 🔬 Strategy 2: Imitation Bootstrapped RL (IBRL)

A complementary approach that keeps the IL policy frozen and uses it as a guide during RL training.

### How It Works

1. Train an IL policy on demonstrations (behavior cloning)
2. During RL rollouts, at each step flip a coin between the IL policy action and the RL policy action (with annealing)
3. During critic training, use the IL policy to propose alternative target actions for bootstrapping Q-values
4. The RL policy is a separate network that learns from scratch, benefiting from the IL policy's exploration guidance

### Key Findings

| Finding                                                                 | Implication                                          |
| ----------------------------------------------------------------------- | ---------------------------------------------------- |
| 6.4x higher success rate than RLPD with 10 demos and 100K interactions  | Extremely sample-efficient when demo budget is small |
| IL policy proposes actions for both exploration and value bootstrapping | Two mechanisms of benefit from the IL policy         |
| Works with frozen BC policy; no need for offline RL pretraining         | Directly compatible with ACT or OpenPI checkpoints   |
| IL policy quality directly bounds early RL performance                  | Weak IL policy provides limited guidance             |

### Relevance to the Hackathon

IBRL is directly applicable because it accepts any frozen IL policy as a guide without requiring offline RL pretraining. The ACT or OpenPI policy can serve as the "bootstrapping" policy. Main challenge: action chunking in ACT produces temporally extended actions, while IBRL expects per-step action proposals. OpenPI may be more compatible if it supports single-step querying.

### Reference

> Hu, Mirchandani, Sadigh. "Imitation Bootstrapped Reinforcement Learning." ICLR 2024.
> Code: https://github.com/hengyuan-hu/ibrl

## 🔬 Strategy 3: Jump-Start RL (JSRL)

Curriculum-based approach where the IL policy handles the early portion of each episode and the RL policy handles the rest.

### How It Works

1. Train a guide policy (the IL policy) on demonstrations
2. At the start of each RL episode, the guide policy executes for a variable number of steps (drawn from a geometric distribution)
3. The RL exploration policy takes over for the remainder of the episode
4. Over training, gradually reduce the guide policy's involvement until the RL policy handles full episodes
5. All experience (from both policies) goes into the RL replay buffer

### Key Findings

| Finding                                                                     | Implication                                                 |
| --------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Guide policy provides "free" exploration in hard-to-reach states            | Especially valuable for long-horizon or sparse-reward tasks |
| Geometric distribution over guide steps creates an implicit curriculum      | RL policy gradually learns longer horizons                  |
| Does not require shared architecture between guide and exploration policies | Any IL policy format works as the guide                     |
| WSRL outperforms JSRL on Antmaze and Kitchen (WSRL paper comparison)        | JSRL doesn't leverage Q-function initialization             |

### Relevance to the Hackathon

JSRL is the simplest method to implement for the hackathon. Requirements: (1) the IL policy can produce actions in the Isaac environment, and (2) a switch mechanism between the IL and RL policies mid-episode. Downside: the RL policy starts learning from a cold Q-function with no value initialization.

### Reference

> Uchendu, Xiao, Lu, Zhu, Yan, Simon, et al. "Jump-Start Reinforcement Learning." ICML 2023.

## 🔬 Strategy 4: Diffusion Policy Policy Optimization (DPPO)

RL fine-tuning specifically designed for diffusion-based policies.

### How It Works

1. Pretrain a Diffusion Policy on demonstrations
2. Fine-tune directly using policy gradient (PPO) while preserving the diffusion denoising structure
3. The diffusion parameterization provides structured, on-manifold exploration during RL
4. Value function depends on environment state only, not on the intermediate denoised actions

### Key Findings

| Finding                                                                                             | Implication                                                          |
| --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| PPO fine-tuning of diffusion policies outperforms off-policy Q-learning methods                     | Surprising; PPO was thought to be inefficient for diffusion policies |
| Structured exploration from diffusion leads to more robust behavior and better sim-to-real transfer | Diffusion's denoising structure is a feature, not a bug, for RL      |
| Sweet spot for clipping denoising noise balances exploration vs. action quality                     | Requires tuning the noise clipping hyperparameter                    |
| Zero-shot sim-to-real transfer on assembly tasks                                                    | Directly relevant for Isaac-to-real transfer                         |

### Relevance to the Hackathon

If the team uses OpenPI (a diffusion-based policy), DPPO is the most natural fine-tuning path. It fine-tunes the pretrained policy in-place using PPO, which Isaac Lab natively supports. Main challenge: DPPO's per-step inference involves multiple denoising passes, making it slower than MLP-based RL policies in massively parallel Isaac environments.

### Reference

> Ren, Lidard, Ankile, Simeonov, Agrawal, Majumdar, Burchfiel, Dai, Simchowitz. "Diffusion Policy Policy Optimization." ICLR 2025.
> Code: https://diffusion-ppo.github.io

## 🔬 Strategy 5: Residual Policy Learning (RPL / ResFiT)

Learn a small corrective RL policy on top of a frozen IL policy.

### How It Works

1. Freeze the pretrained IL policy (ACT or OpenPI)
2. Train a lightweight residual RL policy whose output is added to the IL policy's actions: `a_final = a_IL + a_residual`
3. The residual policy is a simple MLP trained with standard RL (PPO or SAC)
4. The residual learns to correct errors in the IL policy rather than learning the full task from scratch

### Key Findings

| Finding                                                                                  | Implication                                                                         |
| ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Residual learning can solve long-horizon sparse-reward tasks where RL from scratch fails | IL policy provides the coarse behavior; RL refines it                               |
| Works with non-differentiable base policies                                              | No gradient flow through IL policy required                                         |
| Decoupling pretraining and fine-tuning resolves action-chunking tension                  | ResFiT specifically addresses 870-dim action spaces from action-chunked BC policies |
| Residual action space can be constrained (e.g., clipped to small range)                  | Prevents the RL policy from overriding the IL policy completely                     |

### Relevance to the Hackathon

This is the most pragmatic approach for action-chunked IL policies like ACT. It sidesteps the fundamental incompatibility between ACT's action-chunking (which produces temporal action sequences) and standard per-step RL. The RL residual operates at a per-chunk or per-step level on top of the frozen ACT policy.

### Reference

> Silver, Allen, Tenenbaum, Kaelbling. "Residual Policy Learning." arXiv:1812.06298, 2018.
> Ankile, Simeonov, Ren, Agrawal. "Residual Off-Policy RL for Finetuning Behavior Cloning Policies." arXiv:2509.19301, 2025.

## 🔬 Strategy 6: Demo-Augmented Online RL (RLPD / SERL)

Use recorded episodes directly in the RL replay buffer alongside online experience.

### How It Works

1. Load recorded demonstration episodes into the replay buffer
2. During each RL update, sample 50% from demonstrations and 50% from online experience
3. Train a standard off-policy RL agent (SAC) from scratch, benefiting from the demonstration data as high-quality transitions
4. No pretraining phase; demonstration data informs learning from the first gradient step

### Key Findings

| Finding                                                                                  | Implication                                  |
| ---------------------------------------------------------------------------------------- | -------------------------------------------- |
| RLPD (from scratch with demos in buffer) often outperforms offline-to-online fine-tuning | The simplest baseline is surprisingly strong |
| SERL extends this to real robots with human intervention checkpoints                     | Proven on real Franka manipulation tasks     |
| Does not use pretrained policy or Q-function initialization                              | No warm-start benefit from IL pretraining    |
| WSRL outperforms RLPD by leveraging pretrained initialization                            | Room for improvement over this baseline      |

### Relevance to the Hackathon

This is the simplest baseline: dump the recorded Isaac episodes into an SAC replay buffer and start training. No IL pretraining needed. Useful as a comparison point. However, it does not achieve the goal of using an IL policy to warm-start RL.

### Reference

> Ball, Smith, Kostrikov, Levine. "Efficient Online Reinforcement Learning with Offline Data." arXiv:2302.02948, 2023.
> Luo, Hu, Xu, Tan, Berg, Sharma, Schaal, Finn, Gupta, Levine. "SERL: A Software Suite for Sample-Efficient Robotic Reinforcement Learning." arXiv:2401.16013, 2024.

## � Strategy 7: VLA-RL (PPO Fine-Tuning of Vision-Language-Action Models)

Apply on-policy RL (PPO) directly to a pretrained auto-regressive VLA, treating robotic manipulation as multi-turn conversation.

### How It Works

1. Start from a pretrained VLA (OpenVLA-7B, fine-tuned via SFT on demonstrations)
2. Formulate each manipulation trajectory as a multi-modal multi-turn conversation: at each timestep the VLA receives an image + language instruction and produces action tokens auto-regressively
3. Fine-tune the VLA with PPO using LoRA adapters, keeping the base model frozen
4. Train a separate critic (value model) with the same VLA architecture; warm up the critic for several iterations on rollouts from the frozen SFT policy before starting joint actor-critic optimization
5. Use a Robotic Process Reward Model (RPRM) to densify sparse environment rewards: a frozen VLM fine-tuned on pseudo-reward labels extracted from successful trajectories (milestone segmentation via gripper state changes, progress labeling via end-effector velocity keyframes)
6. Apply a curriculum selection strategy that prioritizes tasks at ~50% success rate: $P(\text{task}_j) \propto \exp((0.5 - s_j) / \tau)$

### Key Findings

| Finding                                                                                    | Implication                                                                                                     |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| VLA-RL improves OpenVLA-7B by 4.5% over SFT on 40 LIBERO tasks, matching π₀-FAST           | RL fine-tuning of large VLAs is viable and competitive with frontier commercial models                          |
| Critic warmup is essential: without it, success rate drops from 90.2% to 80.0%             | Inaccurate early value estimates destabilize policy gradients; must pretrain the critic on SFT policy rollouts  |
| RPRM reward densification improves success from 85.8% to 90.2% over sparse reward          | Sparse rewards alone are insufficient for long-horizon manipulation with large models                           |
| Curriculum selection outperforms uniform task sampling (88.0% → 90.2%)                     | Focusing on tasks at the frontier of capability maximizes learning signal                                       |
| Episode lengths decrease during training                                                   | The RL policy learns more efficient action sequences, unlike LLM RL where longer reasoning improves performance |
| Performance scales with test-time compute (more RL training steps → monotonic improvement) | Early evidence of inference-time scaling laws in robotics                                                       |
| RL-trained actions cover the action space more uniformly than SFT demonstrations           | RL exploration overcomes the narrow demonstration distribution, improving robustness                            |
| Total training cost: 48 GPU hours to match π₀-FAST                                         | Practical compute budget for a hackathon-scale effort                                                           |

### Architecture Details

| Component      | Specification                                                      |
| -------------- | ------------------------------------------------------------------ |
| Base model     | OpenVLA-7B (Llama-2-7B backbone + SigLIP + DinoV2 visual encoders) |
| RL algorithm   | PPO with GAE, clipped surrogate objective                          |
| Fine-tuning    | LoRA adapters on the VLA; merge weights for inference              |
| Critic         | Separate VLA-architecture value model, state-only                  |
| Reward model   | Frozen VLM fine-tuned as RPRM on pseudo-labels                     |
| Inference      | vLLM-accelerated batch decoding on dedicated GPU                   |
| Training infra | Ray + PyTorch FSDP, GPU-balanced vectorized environments           |

### Relevance to the Hackathon

VLA-RL is the most directly relevant strategy if the team uses an auto-regressive VLA (like OpenVLA or a similar model) rather than a pure IL policy like ACT. It demonstrates that PPO can effectively fine-tune a 7B-parameter VLA in simulation within a practical compute budget (48 GPU hours). The key innovations that make this work are:

- Critic warmup: train the value model on frozen-policy rollouts before joint optimization (prevents the early instability that plagues naive PPO fine-tuning of large models)
- RPRM reward densification: avoids the need for hand-crafted dense reward functions by automatically extracting progress signals from successful trajectories
- Curriculum: focus training on tasks at the boundary of the policy's capability

For the KUKA hackathon, VLA-RL is relevant if the IL policy is a VLA rather than a standalone ACT/OpenPI checkpoint. The auto-regressive token-based action space is different from the continuous action spaces that ACT and standard RL operate on. If the team trains OpenVLA on their KUKA demonstration episodes via SFT first, VLA-RL provides a direct path to RL fine-tuning using PPO, which Isaac Lab supports natively.

Main limitation: VLA-RL's RPRM relies on heuristic pseudo-reward extraction (gripper state changes, velocity keyframes) which may not transfer cleanly to all task types.

### Reference

> Lu, Guo, Zhang, Zhou, Jiang, Gao, Tang, Wang. "VLA-RL: Towards Masterful and General Robotic Manipulation with Scalable Reinforcement Learning." arXiv:2505.18719, 2025.
> Code: https://github.com/GuanxingLu/vlarl

## 📊 Strategy Comparison

| Strategy | Uses IL Policy Weights | Uses IL for Exploration | Requires Offline RL | Action Chunking Compatible | Implementation Complexity | Isaac Lab Integration  |
| -------- | ---------------------- | ----------------------- | ------------------- | -------------------------- | ------------------------- | ---------------------- |
| WSRL     | Yes (via offline RL)   | Warmup only             | Yes (CalQL)         | Requires adaptation        | Medium                    | Custom SAC needed      |
| IBRL     | No (frozen guide)      | Yes (during rollouts)   | No                  | Limited                    | Medium                    | Custom SAC needed      |
| JSRL     | No (frozen guide)      | Yes (episode prefix)    | No                  | Yes (guide runs chunks)    | Low                       | Compatible with any RL |
| DPPO     | Yes (direct fine-tune) | Via diffusion noise     | No                  | N/A (diffusion-native)     | High                      | PPO available natively |
| Residual | No (frozen base)       | Via residual actions    | No                  | Yes (operates on output)   | Low                       | Compatible with any RL |
| RLPD     | No                     | No (demos in buffer)    | No                  | Yes (data only)            | Low                       | Custom SAC needed      |
| VLA-RL   | Yes (LoRA fine-tune)   | Via PPO rollouts        | No                  | N/A (token-based actions)  | High                      | PPO available natively |

## 🎯 Recommended Approach for the Hackathon

Given the constraints (one-week hackathon, KUKA + Isaac environment, ACT or OpenPI as IL policies), a tiered approach maximizes the chance of success.

### Tier 1: Baseline (day 1)

Use JSRL or Residual Policy Learning. Both accept any frozen IL policy and require minimal integration work.

- If the IL policy is ACT with action chunking: use Residual Policy Learning
- If the IL policy can be queried per-step: use JSRL with PPO in Isaac Lab

### Tier 2: Intermediate (days 2-3)

Implement IBRL with the frozen IL policy as the bootstrapping oracle. This provides both exploration guidance and value bootstrapping benefits beyond what JSRL offers.

### Tier 3: Advanced (days 4-5, stretch goal)

For OpenPI (diffusion-based): implement DPPO to directly fine-tune the pretrained diffusion policy with PPO. This provides the tightest integration between IL pretraining and RL fine-tuning.

For ACT: train CalQL offline on the recorded episodes, then apply WSRL for online fine-tuning. This requires the most infrastructure but yields the strongest performance based on the WSRL paper results.

## ⚠️ Critical Pitfalls

| Pitfall                                                                      | Mitigation                                                              |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Catastrophic forgetting when directly fine-tuning IL policy with RL          | Use warmup phase (WSRL) or keep IL policy frozen (IBRL, JSRL, Residual) |
| Action chunking incompatibility between IL and RL                            | Use Residual approach or re-engineer to per-step policy                 |
| Q-value divergence ("downward spiral") when switching from offline to online | Warmup rollouts from frozen pretrained policy seed the replay buffer    |
| Reward shaping mismatch between IL objective and RL reward                   | Ensure RL reward signal is dense enough for the chosen method           |
| Isaac parallelism assumptions broken by off-policy methods                   | PPO-based methods (DPPO, JSRL with PPO) are simpler to parallelize      |

## 📚 Full Reference List

| Paper                                                                            | Year | Venue            | Relevance                                            |
| -------------------------------------------------------------------------------- | ---- | ---------------- | ---------------------------------------------------- |
| WSRL: Efficient Online RL Fine-Tuning Need Not Retain Offline Data (Zhou et al.) | 2024 | arXiv:2412.07762 | Primary reference for no-retention fine-tuning       |
| IBRL: Imitation Bootstrapped Reinforcement Learning (Hu et al.)                  | 2024 | ICLR 2024        | IL policy as frozen guide for RL                     |
| JSRL: Jump-Start Reinforcement Learning (Uchendu et al.)                         | 2023 | ICML 2023        | Curriculum via guide policy roll-in                  |
| DPPO: Diffusion Policy Policy Optimization (Ren et al.)                          | 2025 | ICLR 2025        | PPO fine-tuning for diffusion policies               |
| Residual Policy Learning (Silver et al.)                                         | 2018 | arXiv:1812.06298 | Additive RL correction over frozen base policy       |
| ResFiT: Residual Off-Policy RL for Finetuning BC (Ankile et al.)                 | 2025 | arXiv:2509.19301 | Modern residual approach for action-chunked policies |
| RLPD: Efficient Online RL with Offline Data (Ball et al.)                        | 2023 | arXiv:2302.02948 | Demos-in-buffer baseline                             |
| SERL: Sample-Efficient Robotic RL (Luo et al.)                                   | 2024 | arXiv:2401.16013 | Real robot RL with demonstration data                |
| CalQL: Calibrated Offline RL Pre-Training (Nakamoto et al.)                      | 2024 | NeurIPS 2024     | Calibrated Q-functions for fine-tuning               |
| CQL: Conservative Q-Learning (Kumar et al.)                                      | 2020 | NeurIPS 2020     | Pessimistic offline RL baseline                      |
| IQL: Implicit Q-Learning (Kostrikov et al.)                                      | 2021 | arXiv:2110.06169 | In-sample offline RL                                 |
| ACT: Action Chunking with Transformers (Zhao et al.)                             | 2023 | RSS 2023         | IL policy architecture under consideration           |
| Isaac Lab (Mittal et al.)                                                        | 2025 | arXiv:2511.04831 | Simulation framework for the hackathon               |
| VLA-RL (Chen et al.)                                                             | 2025 | arXiv:2505.18719 | RL fine-tuning for VLA foundation models             |
| Pretraining in Actor-Critic RL for Robot Motion (2025)                           | 2025 | arXiv:2510.12363 | Warm-starting actor-critic from pretraining          |
