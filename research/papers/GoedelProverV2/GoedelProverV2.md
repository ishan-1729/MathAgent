# Goedel-Prover-V2: Scaling Formal Theorem Proving with Scaffolded Data Synthesis and Self-Correction

Yong Lin, Shange Tang, Bohan Lyu, Ziran Yang, Jui-Hui Chung, Haoyu Zhao, Lai Jiang, Yihan Geng, Jiawei Ge, Jingruo Sun, Jiayun Wu, Jiri Gesi, Ximing Lu, David Acuna, Kaiyu Yang, Hongzhou Lin, Yejin Choi, Danqi Chen, Sanjeev Arora, Chi Jin

## Abstract

We introduce Goedel-Prover-V2, a series of open-source language models that set a new state-of-the-art in automated theorem proving. Built on the standard expert iteration and reinforcement learning pipeline, our approach incorporates three key innovations: (1) Scaffolded data synthesis: We generate synthetic tasks of increasing difficulty to train the model to master increasingly complex theorems; (2) Verifier-guided self-correction: We enable the model to iteratively revise its proofs by leveraging feedback from the Lean compiler; (3) Model averaging: We merge model checkpoints to mitigate the decrease in model output diversity in later stages of training. Our small model, Goedel-Prover-V2-8B, reaches 84.6% pass@32 on MiniF2F and outperforms DeepSeek-Prover-V2-671B under the same metric, despite being 80X smaller. Our flagship model, Goedel-Prover-V2-32B, achieves 88.1% on MiniF2F at pass@32 in standard mode and 90.4% in self-correction mode, outperforming prior SOTA by a large margin. Additionally, our flagship model solves 86 problems on PutnamBench at pass@184, securing the first place among open-source models on the leaderboard, surpassing DeepSeek-Prover-V2-671B’s record of solving 47 problems by pass@1024 with a significantly smaller model size and compute budget. At the time of its release (July–August 2025), Goedel-Prover-V2 achieves the strongest overall performance among all open-source theorem provers. It also ranks among the top-performing models---including closed-source systems with publicly reported performance---under a constrained test-time compute budget. Our models, code, and data are released at https://github.com/Goedel-LM/Goedel-Prover-V2.

## 1 Introduction

Automated theorem proving (ATP) is a grand challenge for AI, requiring the construction of step-by-step, machine-verifiable proofs in formal languages like Lean. Unlike reasoning in natural language, ATP demands a completely rigorous logical flow, which poses an exceptional challenge for AI systems. The field has advanced rapidly in recent years; for example, DeepMind's AlphaProof and AlphaGeometry demonstrated that AI systems are capable of achieving IMO (International Math Olympiad) medal-level performance. Furthermore, open-source efforts such as DeepSeek-Prover-V2 and Kimina‑Prover have achieved impressive results on benchmarks like MiniF2F and PutnamBench, demonstrating the effectiveness of leveraging the reasoning ability of LLMs through chain-of-thoughts. These successes, however, typically depend on massive models (e.g., DeepSeek-Prover-V2 has 671B parameters) or computationally intensive inference (e.g., the concurrent work Seed-Prover), which involves complex search algorithms and enormous search budgets.

In this work we introduce Goedel-Prover-V2, a new series of open-source models for automated theorem proving in Lean that establish a new state-of-the-art in both performance and computational efficiency. The models are capable of generating whole proofs and leveraging verifier feedback for iterative self-correction. Our flagship 32B model achieves 88.1% pass@32 on the MiniF2F benchmark, improving to 90.4% with self-correction. This performance surpasses both the concurrent 72B Kimina-Prover and the previous SOTA 671B DeepSeek-Prover-V2, while using significantly fewer parameters (Figure 1). On the more challenging PutnamBench, our model solves 44 problems (57 with self-correction), more than doubling the number solved by DeepSeek-Prover-V2 under the same metric. The efficiency of our approach is further underscored by our 8B model, which alone outperforms the 671B DeepSeek-Prover-V2 on MiniF2F despite being nearly 80 times smaller. At the time of its release (July–August 2025), Goedel-Prover-V2 achieves the strongest overall performance among all open-source theorem provers. It also ranks among the top-performing models---including closed-source systems with publicly reported performance---under small test-time compute budgets.

![Figure 1: Performance of Goedel-Prover-V2 on different benchmarks under pass@32.](images/000_combined_performance_plots_varied_width.png)

[Figure 1](images/000_combined_performance_plots_varied_width.png): Performance of Goedel-Prover-V2 on different benchmarks under pass@32.

The key to these gains lies in novel design across the framework, data, and training pipelines. We highlight the main components below:

- **Verifier-guided self-correction:** Framework-wise, we incorporate feedback from the Lean compiler (verifier) into the model input to enable the error-correction ability of our theorem prover (*verifier-guided self-correction*). While correcting errors based on verifier feedback has been studied in theorem proving and coding, we further incorporate this into models that generate long chain-of-thought (CoT) reasoning, which is effective for complex reasoning tasks such as ATP.
- **Scaffolded data synthesis:** Successfully combining long CoT with verifier-guided error correction requires special efforts on curating data. In addition to formalizing existing math problems and curating data for error correction, we augment our training statement set through *scaffolded data synthesis*. This technique creates math problems at an appropriate difficulty level to provide the model with better learning signals.
- **Model averaging:** Our training recipe extends beyond standard expert iteration and reinforcement learning. We also apply *model averaging* to mitigate the decrease in model output diversity that can occur in the later stages of training.

Overall, our results demonstrate that the frontier of formal theorem proving can be advanced without access to extremely large models, vast computational resources, or proprietary technology. We hope that our open-source theorem prover series, Goedel-Prover-V2, will enable the community to build upon them and accelerate progress toward AI systems that can reliably solve and verify complex mathematical problems, and ultimately bridge the long-standing divide between intuitive human reasoning and formal proof verification.

The paper is organized as follows: in Section 2, we introduce our main methods for training Goedel-Prover-V2, including the verifier-guided self-correction framework (Section 2.1), scaffolded data synthesis (Section 2.2), and the training pipeline (Section 2.3). In Section 3, we present the evaluation results on Goedel-Prover-V2, including both standard and curated benchmarks (Section 3.3), scaling behavior under different test-time budgets (Section 3.4), and a study of reinforcement learning and model averaging (Section 3.6). Finally, we review prior work (Section 4) and discuss the connection with Goedel-Prover-V2.

## 2 Method

In this section, we present our method in detail. We start with the key framework innovation compared to the previous vanilla whole-proof generation methods (e.g. DeepSeek-Prover-V2) by utilizing the feedback from the Lean compiler to guide the proof-correction procedure (Section 2.1). Then, in Section 2.2, we demonstrate how to curate the training data (statements), with an emphasis on augmenting the data through scaffolded data synthesis. Based on the curated data, we present the details of training our theorem prover in Section 2.3, which includes supervised fine-tuning (SFT), reinforcement learning (RL), and model averaging. Section 2.4 provides a summary of the overall framework.

### 2.1 Chain-of-Thought and Self-Correction

Prevailing paradigms for whole-proof generation in automated theorem proving have largely been "end-to-end," where a model generates a complete formal proof from a theorem statement in a single pass. Recent work has significantly advanced this approach by leveraging the long-chain-of-thought reasoning capabilities of large models.

A key distinction between informal and formal proof generation lies in the availability and utilization of compilation (verification) feedback from proof assistants like Lean or Coq. Humans naturally leverage such feedback to iteratively revise their proofs. Recent works have shown that integrating the verifier's error messages or tactic verification outcomes into proof generation significantly improves synthesis accuracy.

Our framework formalizes this intuition by explicitly incorporating verifier feedback within the whole-proof generation loop. We structure the pipeline so that, after an initial proof attempt, verification failures are parsed and communicated back into the model as corrective guidance. The model then generates proof repairs, leading to an iterative self‑correction process.

### 2.2 Curating Formal Statements

In this part, we detail our methods for curating a large, high-quality dataset of Lean 4 statements, which is essential for expert iteration and RL in formal theorem proving. We start with the formalizer training, which is the core of translating existing math problems written in natural language into Lean statements. We then present our scaffolded data synthesis pipeline that creates math problems at an appropriate difficulty level, which includes a lightweight formal-based method that utilizes the Lean system, and an informal-based system for large-scale data augmentation.

**Table 1.** Comparison of different formalizers on 300 Omni-math problems.

| **Name** | **Pass** | **Failed** |
| --- | --- | --- |
| Kimina-autoformalizer | 161 | 139 |
| Goedel-Formalizer-V2 | 228 | 72 |

**Formalizer Training.**

Existing Lean datasets (e.g., Goedel-Pset-v1) contain many low-quality problems—human evaluation on a sampled subset shows that over 80% of unsolved problems are incorrectly formalized. This highlights the need for stronger formalizers. We train our formalizer using the standard expert iteration pipeline, which combines Lean syntax checks and semantic evaluation via LLMs to assess translation quality. Only statements that pass both checks are included in the next iteration of SFT. Details of the LLM semantic evaluation prompt are provided in Appendix C.

To initialize expert iteration, we prompt Claude Sonnet 4 to generate 50K formalized statements, along with corresponding reasoning traces. Incorporating reasoning capability enables our formalizer to outperform previous models such as Goedel-Formalizer-V1 and Kimina-Autoformalizer, which lack this feature.

We evaluate our model Goedel-Formalizer-V2 and Kimina-Autoformalizer on a benchmark of 300 OmniMath problems. Results are shown in Table 1.

![Figure 2: Our informal-based scaffolded data synthesis pipeline with three parts: (1) informal statement generation; (2) formalization and quality checking; and (3) negation and difficulty filtering.](images/001_goedel-prover-V2-datasynthesis.png)

[Figure 2](images/001_goedel-prover-V2-datasynthesis.png): Our informal-based scaffolded data synthesis pipeline with three parts: (1) informal statement generation; (2) formalization and quality checking; and (3) negation and difficulty filtering.

**Formal-based scaffolded data synthesis.**

When the prover fails to find a proof for a challenging problem, we can still leverage the proof attempt to generate simpler, related problems. The intuition is that even an incorrect proof attempt may introduce valid subgoals that represent easier subproblems. We utilize a powerful tactic in Lean, $\texttt{extract\_goal}$, to capture the unsolved states of a proof. These extracted goals (together with the preconditions), which are well-formed mathematical statements, are then used to augment our training data for the next phase. Since an extracted statement is not guaranteed to be provable, we also include its negation, thereby training the model to recognize both true and false propositions.

**Informal-based scaffolded data synthesis.**

Another way of scaffolded data synthesis is to leverage the current LLMs' mathematical reasoning ability in natural language, to create math statements at the right level for the model to learn. For a given problem, we prompt an LLM (Qwen3-32B) to generate simpler/sub-problems if it is unsolved, or harder variants if it is already solved. To improve generation quality, we first have the LLM attempt a natural language solution to the original problem, using its output as context. The generated informal statements are then formalized into Lean by the formalization pipeline we described in **Formalizer Training**. To avoid the inference overhead of incorrect or trivial statements, we use an LLM-based filter that assesses each statement for correctness and difficulty, where trivial or mathematically incorrect statements are discarded (the negation of incorrect statements are also added to the dataset). This filtering process significantly accelerates data synthesis, with a minor trade-off in potentially discarding some valid statements due to LLM judgment errors. The prompts and further details are in Appendix D.

### 2.3 Training Algorithms

**Supervised fine-tuning and expert iteration.**

We follow the standard pipeline of Expert Iteration by iterating between using the model to conduct large-scale inference on the statement sets, collecting correct proofs with reasoning traces, and fine-tuning the model further by Supervised Fine-tuning (SFT) on the collected samples.

**Reinforcement learning.**

We aim to train a model capable of generating complete proofs and correcting its own errors via verifier-guided self-correction. To this end, we adopt an efficient hybrid strategy. We first sample multiple complete proofs in parallel. For each sampled proof, we perform serial self-correction, generating one correction per round. This serial correction process aligns well with RL, which optimizes expected reward and has been shown to improve single-shot (pass@1) accuracy.

Our RL implementation adopts a multi-task setup (illustrated in Appendix Figure 9): 50% of the inputs are used for whole proof generation, and the remaining 50% for first-round self-correction. We train for a single epoch, using approximately 46K and 67K unique inputs for the 8B and 32B models, respectively. On the algorithmic side, we use a hybrid GRPO-based approach. Compared to vanilla GRPO, our method removes group normalization as suggested by Dr.GRPO to avoid inherent bias on length, incorporates clip-higher, overlong penalties, and dynamic sampling from DAPO, and excludes the KL regularization term from the objective to encourage exploration.

A key observation is that question difficulty significantly impacts RL training. To address this, we modify the dynamic sampling strategy to only include problems with pass rates in the range (0, 0.75] during optimization. For further implementation details, see Appendix E.1.

**Model averaging for enhanced diversity.**

We observed that in the later stages of SFT and RL, the model's diversity decreases. This is reflected by an increase in pass@1 but a decline in pass@N for larger values of N, such as N=32. We adopt model averaging to enhance model diversity. Specifically, let the parameters of the base model be denoted as $\theta_0$, and those of the fine-tuned model as $\theta$. We use the combined model parameters defined as $(1-\alpha)\theta_0 + \alpha \theta$, where $\alpha \in (0, 1)$. Existing literature has demonstrated that this simple approach can significantly improve the feature diversity of the final model. Our observations also confirm that this method effectively enhances pass@N. We perform model averaging at each stage of the process. Specifically, we apply model averaging after completing SFT and use the averaged model as the starting point for RL. Once RL is completed, we perform model averaging again and use this averaged model as the final model.

### 2.4 Whole pipeline: putting everything together

![Figure 3: The overall workflow of model training. "+AVG" denotes that the trained model is averaged with the base model after training. "RL-AVG" is the final output model. Detailed descriptions are provided in Section 2.4](images/002_framework_flow4.png)

[Figure 3](images/002_framework_flow4.png): The overall workflow of model training. "+AVG" denotes that the trained model is averaged with the base model after training. "RL-AVG" is the final output model. Detailed descriptions are provided in Section 2.4.

The pipeline consists of the following steps:

- We utilize DeepSeek-Prover-V2-7B and DeepSeek-Prover-V2-671B to perform large-scale inference, producing an initial supervised fine-tuning (SFT) dataset $S_1$ for complete proof generation.
- We employ $S_1$ to conduct SFT on both Qwen3-8B and Qwen3-32B, resulting in fine-tuned models SFT-$S_1$ for both the 8B and 32B variants. We use [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) for SFT.
- Using SFT-$S_1$ and DeepSeek-Prover-V2-671B, we annotate self-correction data. The self-correction data from DeepSeek-Prover-V2-671B is generated using [NeMo-Skills](https://github.com/NVIDIA/NeMo-Skills) with 144 H100s. This data is then incorporated back into $S_1$ to create an enhanced dataset $S_2$. We subsequently perform SFT on both model sizes using $S_2$, yielding improved models SFT-$S_2$. We perform model averaging between SFT-$S_2$ and the base model for both the 8B and 32B models, resulting in SFT-$S_2$-AVG.
- We perform scaffolded data synthesis with SFT-$S_2$-AVG to generate the dataset $S_3$. Continuing the SFT process, we further improve the models by training SFT-$S_2$-AVG on $S_3$, yielding SFT-$S_3$, and subsequently obtain the averaged model SFT-$S_3$-AVG.
- We apply reinforcement learning to SFT-$S_3$-AVG and then conduct model averaging to obtain the final model RL-AVG.

The overall workflow is illustrated in Figure 3.

## 3 Evaluation

This section presents our experiment results on Goedel-Prover-V2. We first discuss our evaluation benchmarks (Section 3.1). Then, we discuss our main evaluation results on the selected benchmarks (Section 3.3), and correspondingly the scaling behavior (Section 3.4). Finally, we investigate the performance of reinforcement learning and model averaging (Section 3.6).

### 3.1 Benchmarks

**MiniF2F.** MiniF2F consists of 488

problem statements (244 validation and 244 test problems) in Lean. The problems are drawn from high-school level competitions including the AMC, AIME, and the International Mathematical Olympiad (IMO). We use the version of MiniF2F provided by Kimina (https://huggingface.co/datasets/AI-MO/minif2f_test), which has some incorrect statements fixed.

**PutnamBench.** PutnamBench focuses on college-level mathematics competition problems that are sourced from the William Lowell Putnam Mathematical

Competition years 1962 - 2023. PutnamBench comprises 644 problems, covering algebra, analysis, number theory, geometry, combinatorics, probability, and set theory.

**MathOlympiadBench.**

We constructed MathOlympiadBench, which comprises 360 human-verified formalizations of Olympiad-level mathematical problems, sourced from Compfiles (https://dwrensha.github.io/compfiles/imo.html) and IMOSLLean4 repository (https://github.com/mortarsanjaya/IMOSLLean4). It contains 158 IMO problems from 1959 to 2024, 131 IMO shortlist problems covering 2006 to 2023, 68 national mathematical Olympiad problems, and 3 additional mathematical puzzles. The statistic of problem categories in MathOlympiadBench is presented in Figure 4, and Figure 5 visualizes humans' formalizing and solving status of IMO problems in MathOlympiadBench. See Appendix A for more details of MathOlympiadBench and its comparison with MiniF2F.

![Figure 4: Distribution of problems in MathOlympiadBench by category.](images/003_category_pie_chart.png)

[Figure 4](images/003_category_pie_chart.png): Distribution of problems in MathOlympiadBench by category.

![Figure 5: Community's achievement on IMO problems by year and problem index. Each cell represents a problem: gray = statement not formalized, red = not solved, and green = solved.](images/004_imo_solved_matrix.png)

[Figure 5](images/004_imo_solved_matrix.png): Community's achievement on IMO problems by year and problem index. Each cell represents a problem: gray = statement not formalized, red = not solved, and green = solved.

### 3.2 Evaluated Methods

Following previous works the evaluations are done under Lean 4.9.0-rc1. For the first round of whole-proof generation, the max token length of the model is set to be 30,000. For the verifier-guided error-correction, we sequentially conduct 2 additional rounds of self-correction, given the verifier's feedback on the previous attempt. The total number of tokens in the self-correction mode is set to be 40,000. We report the pass@N metric.

### 3.3 Main results

**Table 2.** Performance of different whole-proof generation methods on MiniF2F test split.

| Method | Budget | Performance |
| --- | --- | --- |
| Goedel-Prover-SFT | 32 | 57.6%$+/-$0.7% |
|  | 3200 | 62.7% |
| STP | 128 | 61.2%$+/-$0.6% |
|  | 3200 | 65.0%$+/-$0.5% |
|  | 25600 | 67.6% |
| Kimina-Prover-Preview-72B | 32 | 68.85% |
|  | 8192 | 80.74% |
| DeepSeek-Prover-V2-7B | 32 | 75.6%$+/-$0.5% |
|  | 8192 | 82.0% |
| DeepSeek-Prover-V2-671B | 32 | 82.4%$+/-$0.6% |
|  | 8192 | 88.9% |
| Kimina-Prover-8B^ | 32 | 78.3% |
| Kimina-Prover-70B^ | 32 | 84.0% |
|  | 1024 | 87.7% |
| w/ TTRL | unknown | 92.2% |
| Goedel-Prover-V2-8B | 32 | 84.6%$+/-$0.6% |
|  | 1024 | 87.9% |
|  | 8192 | 90.2% |
| w/ self-correction | 32 | 86.7%$+/-$0.2% |
|  | 1024 | 89.3% |
| Goedel-Prover-V2-32B | 32 | 88.1%$+/-$0.8% |
|  | 1024 | 91.8% |
|  | 8192 | 92.2% |
| w/ self-correction | 32 | 90.4%$+/-$0.6% |
|  | 1024 | 92.6% |

The evaluation results of Goedel-Prover-V2 on MiniF2F are summarized in Table 2, and results on PutnamBench are shown in Table 3. The results for MathOlympiadBench are presented in the rightmost panel of Figure 1. Below, we summarize and discuss the results.

**High performance at modest scale.**

Our 32B model achieves pass@32 accuracy of 88.1% on MiniF2F, with 90.4% after 2 rounds of error-correction, exceeding the previous state-of-the-art DeepSeek‑Prover‑V2‑671B’s 82.4% while using far fewer parameters. Even the 8B variant achieves 84.6%, nearly matching or outperforming Kimina‑Prover‑70B’s results under the same inference budget, and outperforming the previous SOTA DeepSeek-Prover-V2-671B on MiniF2F, with a significantly smaller model size. On PutnamBench, our 32B model solves 43 problems under pass@32, nearly doubling the DeepSeek-Prover-V2-671B model's performance under the same budget. With error-correction, Goedel-Prover-V2-32B solves 57 problems under pass@32, outperforming DeepSeek-Prover-V2-671B under pass@1024. Under pass@184 and error correction, Goedel-Prover-V2-32B solves 86 on PutnamBench, securing the best open-source theorem prover on the leaderboard.

**Table 3.** Comparison of different models on PutnamBench. Our Goedel-Prover-V2 with compiler-guided self-correction solves 86 problems from PutnamBench, improving the previous SOTA (DeepSeek-Prover-V2) by 39 more problems, and securing the best open-source model on the leaderboard.*

| **#** | **Model** | **num-solved** | **open-source** | **compute** |
| --- | --- | --- | --- | --- |
| 1 | **Goedel-Prover-V2 (self-correction mode)** | 86 | yes | pass@184 |
| 1 | **Goedel-Prover-V2 (self-correction mode)** | 57 | yes | pass@32 |
| 1 | **Goedel-Prover-V2** | 43 | yes | pass@32 |
| 2 | DeepSeek-Prover-V2 | 47 | yes | pass@1024 |
| 2 | DeepSeek-Prover-V2 | 22 | yes | pass@32 |
| 3 | DSP+ | 23 | yes | pass@128 |
| 4 | Bourbaki | 14 | yes | pass@512 |
| 5 | Kimina-Prover-7B-Distill | 10 | yes | pass@192 |
| 6 | Self-play Theorem Prover | 8 | yes | pass@3200 |
| 7 | Goedel-Prover-SFT | 7 | yes | pass@512 |
| 8 | ABEL | 7 | no | pass@596 |

Notes: A concurrent work, Seed Prover, successfully solved 331 problems on PutnamBench. However, the prover is not open-source, and it is not clear what the computational budget is at test time (which is expected to be much larger than ours).

**Efficacy of verifier‑guided self‑correction.**

Adding self‑correction provides a consistent gain of approximately 2 percentage points in pass@32 for both models on MiniF2F. On PutnamBench, error correction leads to 14 more solves under pass@32. This demonstrates that integrating Lean compiler feedback into a long-chain-of-thought revision pipeline enables the model to identify errors and repair them effectively.

**Sample-efficient inference.**

Unlike prior models such as Kimina‑Prover and DeepSeek‑Prover-V2, which rely heavily on large sampling budgets or test-time reinforcement learning to reach peak accuracy, Goedel‑Prover‑V2 attains very high pass@N with minimal inference overhead (N=32 or 64), indicating that the model internalizes powerful reasoning strategies during training. The sample efficiency, together with the relatively small size, makes Goedel-Prover-V2 series a very good candidate for the community to develop new algorithms and test on different benchmarks.

### 3.4 Scaling Analysis

![Figure 6: Scaling behavior on MiniF2F test split.](images/005_inference_scale_performance.png)

[Figure 6](images/005_inference_scale_performance.png): Scaling behavior on MiniF2F test split.

Figure 6 and Table 4 illustrate the scaling behavior of Goedel-Prover-V2 (8B and 32B variants) across different inference budgets, compared against DeepSeek-Prover-V2 (7B and 671B) and Kimina-Prover-72B. At the lower sampling regime (pass@32), Goedel-Prover-V2-32B already achieves 88.1%, notably surpassing DeepSeek-Prover-V2-671B (82.4%) and Kimina-Prover-72B (84.0%). This advantage persists across inference budgets, with our self-correction mode providing approximately a 2-point performance improvement under pass@32 and pass@64, peaking at 92.6% at pass@8192. The smaller 8B model also demonstrates strong scalability, surpassing the 671B DeepSeek model at all budgets.

**Table 4.** The performance (%) of Goedel-Prover-V2 on MiniF2F across different compute budget.

| **Model** | **32** | **64** | **128** | **256** | **512** | **1024** | **2048** | **4096** | **8192** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 32B (self-correction mode) | 90.4 | 91.4 | 91.9 | 92.3 | 92.4 | 92.6 | -- | -- | -- |
| 32B | 88.1 | 89.8 | 90.5 | 91.0 | 91.6 | 91.8 | 92.0 | 92.2 | 92.2 |
| 8B (self-correction mode) | 86.7 | 86.9 | 87.4 | 88.0 | 88.5 | 89.3 | -- | -- | -- |
| 8B | 84.6 | 86.1 | 86.5 | 87.0 | 87.4 | 87.9 | 88.5 | 89.3 | 90.2 |

These results indicate that Goedel-Prover-V2 efficiently internalizes reasoning during training, requiring fewer inference samples to achieve comparable or superior accuracy. Furthermore, the consistent gains from verifier-guided self-correction across all inference budgets further underscore the value of combining error correction with long-chain-of-thought reasoning in formal theorem proving.

![Figure 7: Ablation study on self-correction with extended context length and revision iterations on the MiniF2F test split at pass@32.](images/006_self-corr.png)

[Figure 7](images/006_self-corr.png): Ablation study on self-correction with extended context length and revision iterations on the MiniF2F test split at pass@32.

### 3.5 Analysis of Self-Correction

In our main experiments, self-correction was conducted for a maximum of 2 rounds with a 40k token context length. To further explore its capabilities, we used YaRN to extend the context length to 128k tokens and allowed up to 5 revision iterations. We conducted a series of experiments on the MiniF2F benchmark at pass@32, the results of which are presented in Figure 7. Alongside our standard prompting setup (Self Correction), we performed two ablation studies: (1) removing the specific compiler error messages (w/o Error Messages), and (2) removing the chain-of-thought from previous attempts, retaining only the formal proof (w/o Previous CoTs).

The results show that removing compiler feedback significantly lowers performance, confirming that specific error messages are crucial for effective revision. Similarly, removing the reasoning from previous attempts also slightly degrades performance, indicating that retaining the chain-of-thought from prior rounds is beneficial. Furthermore, with an extended context and more revision iterations, the full self-correction model's pass@32 accuracy on MiniF2F reaches an average of 92.7%, which surpasses the 92.2% performance of the model without self-correction at pass@8192, highlighting the sample efficiency of our iterative revision process.

### 3.6 RL and Model averaging

We systematically evaluate the impact of RL steps and model averaging strategies on the performance of Goedel-Prover-V2. Specifically, for RL checkpoints at steps 60, 80, and 90, we apply model averaging with coefficients $\alpha = 0.6, 0.7, 0.8, 0.9$ (where $\alpha$ is the weight of the base model). We assess each averaged model in both vanilla (whole-proof generation) and correction (with self-correction) settings, evaluating both $\text{pass@1}$ and $\text{pass@N}$, as visualized in Figure 8.

For both vanilla and correction settings, $\text{pass@1}$ consistently increases as the number of RL steps grows. For vanilla $\text{pass@N}$, performance stabilizes at higher RL steps, whereas in the correction setting, $\text{pass@N}$ continues to improve. This indicates that correction benefits more from RL, likely due to the shortage of high-quality self-correction data in the SFT stages.

Examining different values of $\alpha$, we observe that a higher proportion of the base model (i.e., lower $\alpha$) leads to lower $\text{pass@1}$, but $\text{pass@N}$ first rises and then falls as $\alpha$ increases. There exists an optimal model averaging ratio that maximizes $\text{pass@N}$. This trend holds for both vanilla and correction settings, with the improvement being more pronounced for correction. This suggests that model averaging not only improves sample diversity but also amplifies the benefits of RL-driven self-correction.

![Figure 8: The effects of varying RL steps and model averaging ratios on the pass@1 and pass@32 performance of models, both with and without correction.](images/007_rl_avg.png)

[Figure 8](images/007_rl_avg.png): The effects of varying RL steps and model averaging ratios on the pass@1 and pass@32 performance of models, both with and without correction.

## 4 Related Works

**Formal Theorem Proving as Whole Proof Generation.**

Inspired by the success of end-to-end reasoning in informal LLMs, recent foundational work in formal theorem proving has adopted a similar strategy: generating entire Lean proofs in a single pass. While informal reasoning models often struggle with step verification, producing fluent yet logically flawed arguments, formal settings like Lean offer a precise and executable verifier that enforces correctness. Leveraging this, models such as DeepSeek-Prover, Goedel Prover, and Kimina Prover demonstrate that end-to-end generation can produce formally verified proofs that are immediately checkable by the Lean proof assistant. This paradigm retains the global coherence and simplicity of sequence generation while grounding outputs in verifiable formal logic.

**Formal Theorem Proving using Proof Search.**

In contrast to end-to-end generation, proof search–based methods guide the model to incrementally construct proofs by exploring derivation paths with verifier feedback at each step. Recent systems such as InternLM2.5-StepProver, Hunyuan Prover, DeepSeek-Prover v1.5, and BFS Prover employ tree search algorithms---such as Monte Carlo Tree Search or breadth-first search---to explore multiple proof trajectories and iteratively assemble valid proofs. While this strategy improves correctness, it comes at the cost of significantly higher computational overhead, as the model must evaluate and verify a large number of partial branches. Recently, hybrid methods, which use a general-purpose (strong) LLM to write the proof sketch and query theorem provers to fill in the proof for small steps, also emerged (DSP+ and the concurrent work Delta-Prover). However, these works usually require the LLM to be very large and powerful, where DSP+ uses 671B DeepSeek-R1 or DeepSeek-V3, and Delta-Prover uses Gemini. Notably, Seed-Prover employs a wide range of techniques, including extensive test-time search and refinement, and achieves IMO medal-level performance. However, it requires substantial computational resources.

**Self-Repair and Verifier-Guided Refinement.** While tree-based proof search improves correctness, its high computational cost has motivated the development of more efficient mechanisms for guiding search and enabling self-refinement. One direction explores how auxiliary signals or intermediate structures can streamline the reasoning process. Some systems bridge informal and formal reasoning by using informal sketches as a skeleton for formal proof generation, while others employ retrieval-augmented generation (RAG) to retrieve relevant theorems from formal libraries during proof construction. Other approaches implement self-verification and iterative refinement loops, allowing models to autonomously revise candidate proofs using verifier feedback.

Verifier-in-the-loop strategies in recent work also highlight the benefits of interactive feedback, improving success rates through iterative correction rather than brute-force search. These methods collectively point to a more flexible and scalable paradigm: enabling the model to diagnose and repair its own outputs with minimal external supervision.

Goedel-Prover-V2 builds on this trajectory by adopting a self-revision framework, where the model iteratively proposes and refines candidate proofs until they satisfy the Lean checker. This architecture draws inspiration from general-purpose self-repair frameworks in coding and reasoning, bringing their iterative refinement loop into the domain of formal mathematics with long chain-of-thought reasoning.

## 5 Conclusion and Discussion

In this work, we introduced **Goedel-Prover-V2**, a series of state-of-the-art open-source theorem provers that significantly advance automated formal proof generation. Our key innovation is the integration of long-chain-of-thought reasoning with compiler-guided self-correction, addressing a crucial gap overlooked by prior methods. Through scaffolded data synthesis, SFT and RL training, and model averaging, our models achieve state-of-the-art performance among open-source provers. In particular, our 32B model achieves 88.1% on MiniF2F at pass@32 and improves further to 90.4% on MiniF2F at pass@32 with self-correction, outperforming larger models with lower inference cost. Moreover, our 8B model also outperforms the previous SOTA DeepSeek-Prover-V2-671B under pass@32 on MiniF2F with a significantly smaller model size.

We also investigate a proof repair strategy as a test-time alternative to standard pass@k sampling. Instead of regenerating an entire failed proof, our method corrects only the faulty segment. We use Lean 4’s compiler feedback and the $\texttt{extract\_goal}$ tactic to isolate the unsolved subgoal, prompt the prover to solve it independently, and then reinsert the correct solution into the original proof. On the MiniF2F benchmark, this approach improves the amortized budget scaling curve by 1–2 percentage points, highlighting inference-time scaling strategies as a key direction for future work.

By open-sourcing all trained models, we aim to catalyze further advancements in formal reasoning research. We envision that the release of Goedel-Prover-V2 will not only establish a new benchmark for automated theorem proving but also provide a practical, efficient platform upon which future innovations can be built.

## Acknowledgements

This project was the result of a close collaborative effort by all authors, and every major component benefited from joint discussion and iteration. The authors would like to thank Igor Gitman for support with the curation of self-correction data.

HZ and SA acknowledge the support from Schmidt, Darpa, ONR, and NSF. CJ acknowledges the support from NSF-OAC-2411299, NSF-IIS-2239297, and Princeton AI Lab Seed Fund.

## Appendix

## Appendix A. Details of MathOlympiadBench

MathOlympiadBench is human-processed to eliminate several issues presented in the source problems: 1. incomplete problem statements, 2. distribution across multiple files, 3. multiple theorems per problem, and 4. incompatibility with the commonly used Mathlib. The verification process ensures that each problem contains exactly one formal theorem with its corresponding informal statement, and confirms that all formal statements can pass the compilation with the `sorry` tactic.

We compared the IMO problems shared between MathOlympiadBench and MiniF2F, and identified at least 3 cases in MiniF2F exhibiting issues such as: 1. the formal statement to be proved is strictly weaker than the informal statement, and 2. the formal statement does not match the informal statement. Notably, similar issues are not observed for these problems in MathOlympiadBench.

In the following, we present three case studies comparing the problem formalizations in MiniF2F and MathOlympiadBench.

### A.1 IMO 1981, Problem 6: Specific Value vs. General Formula

**MiniF2F**

```lean
/--
The function$f(x,y) $satisfies

(1) $f(0,y)=y+1, $(2) $f(x+1,0)=f(x,1), $(3) $f(x+1,y+1)=f(x,f(x+1,y)), $for all non-negative integers$x,y$. Determine$f(4,1981) $.
-/

theorem imo_1981_p6 (f : ℕ → ℕ → ℕ) 
  (h₀ : ∀ y, f 0 y = y + 1) 
  (h₁ : ∀ x, f (x + 1) 0 = f x 1)
  (h₂ : ∀ x y, f (x + 1) (y + 1) = 
        f x (f (x + 1) y)) : 
  ∀ y, f 4 (y + 1) = 2 ^ (f 4 y + 3) - 3 := by
```

**MathOlympiadBench**

```lean
/-!
# International Mathematical Olympiad 1981, Problem 6

Suppose that f : ℕ × ℕ → ℕ satisfies

 1) f (0, y) = y + 1
 2) f (x + 1, 0) = f (x, 1),
 3) f (x + 1, y + 1) = f (x, f (x + 1, y))

for all x y ∈ ℕ.

Determine f (4, 1981).
-/

def no_eval (x : ℕ) : ℕ := x
abbrev solution_value : ℕ := 
  no_eval ((2^·)^[1984] 1 - 3)

theorem imo1981_p6 (f : ℕ → ℕ → ℕ)
  (h1 : ∀ y, f 0 y = y + 1)
  (h2 : ∀ x, f (x + 1) 0 = f x 1)
  (h3 : ∀ x y, f (x + 1) (y + 1) = 
        f x (f (x + 1) y)) :
  f 4 1981 = solution_value := sorry
```

The informal statement requires computing the exact value of$f(4, 1981) $, a specific number. However, the MiniF2F formalization only asks to prove a general recurrence relation, which is an intermediate step in the solution process. This discrepancy can make the formal proof substantially easier than solving the original problem. In contrast, MathOlympiadBench faithfully translates the original problem into formal language, requiring the proof of the final numerical value as demanded by the IMO statement.

### A.2 IMO 1983, Problem 6: Incomplete vs. Full Condition

**MiniF2F**

```lean
/-- Let$a$, $b$and$c$be the lengths of the sides of a triangle. Prove that$a^2 b(a-b) + b^2 c(b-c) + c^2 a(c-a) \geq 0$.

Determine when equality occurs.
-/

theorem imo_1983_p6 (a b c :$\mathbb{R}$) 
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c) 
  (h₁ : c < a + b) (h₂ : b < a + c)
  (h₃ : a < b + c) : 
  0 ≤ a^2*b*(a-b) + b^2*c*(b-c) + c^2*a*(c-a) := by
```

**MathOlympiadBench**

```lean
/-!
# International Mathematical Olympiad 1983, Problem 6

Suppose that a,b,c are the side lengths of a triangle. Prove that$a^{2}b(a - b) + b^{2}c(b - c) + c^{2}a(c - a) \geq 0.$Determine when equality occurs.
-/

abbrev EqualityCondition (a b c :$\mathbb{R}$) : Prop := 
  a = b ∧ a = c
  
theorem imo1983_p6 
  (T : Affine.Triangle$\mathbb{R}$(EuclideanSpace$\mathbb{R}$(Fin 2))) :
  let a := dist (T.points 1) (T.points 2)
  let b := dist (T.points 0) (T.points 2)
  let c := dist (T.points 0) (T.points 1)
  0 ≤ a^2*b*(a-b) + b^2*c*(b-c) + c^2*a*(c-a) ∧
  (0 = a^2*b*(a-b) + b^2*c*(b-c) + c^2*a*(c-a) ↔
  EqualityCondition a b c) := sorry
```

The informal statement has two parts: proving an inequality and determining the condition for equality. The MiniF2F version only formalizes the inequality, completely omitting the second part of the problem. In contrast, MathOlympiadBench provides a complete formalization.

### A.3 IMO 1962, Problem 2: Informal & Formal Statement Mismatch

**MiniF2F**

```lean
/-- Determine all real numbers no which satisfy the inequality:$\sqrt{\sqrt{3-x}-\sqrt{x+1}}>\dfrac{1}{2}$Show that it is$\left[\, -1,\quad 1-\frac{\sqrt{127}}{32}\, \right) $.
-/

theorem imo_1962_p2 (x :$\mathbb{R}$) (h₀ : 0 ≤ 3 - x) (h₁ : 0 ≤ x + 1)
    (h₂ : 1 / 2 < Real.sqrt (3 - x) - Real.sqrt (x + 1)) : -1 ≤ x ∧ x < 1 - Real.sqrt 31 / 8 := sorry
```

**MathOlympiadBench**

```lean
/-!
# International Mathematical Olympiad 1962, Problem 2

Determine all real numbers x which satisfy$\sqrt{3 - x} - \sqrt{x + 1} > \frac{1}{2}$.
-/

abbrev SolutionSet : Set$\mathbb{R}$:= Set.Ico (-1) (1 -$\sqrt{31}$/ 8)

theorem imo1962_p2 (x :$\mathbb{R}$) :
  x$\in$SolutionSet$\leftrightarrow$x$\leq$3$\land$-1$\leq$x$\land$1/2 <$\sqrt{(3 - x)}$-$\sqrt{(x + 1)}$:= sorry
```

For this problem, the original inequality appears in two different versions (please refer to https://artofproblemsolving.com/wiki/index.php/1962_IMO_Problems/Problem_2): one as $\sqrt{3-x} - \sqrt{x+1} > \frac{1}{2}$ and another as $\sqrt{\sqrt{3-x}-\sqrt{x+1}} > \frac{1}{2}$. In MiniF2F, the informal statement uses the latter (the nested square root version), but the formal statement is based on the former (the simpler difference of square roots), resulting in a mismatch between the informal and formal statements. In contrast, MathOlympiadBench ensures that both the informal and formal statements consistently correspond to the same version of the problem.

## Appendix B. Formal Negation

We attempt to disprove unsolved statements by proving their logical negation. This is achieved by parsing the Lean 4 statements as follows.

```lean
theorem fourIsPrime (a :$\mathbb{N}$) (ha : a = 4) : a.Prime := by sorry
theorem fourIsPrimeNeg : ¬ ∀ (a :$\mathbb{N}$) (ha : a = 4), a.Prime := by sorry
```

## Appendix C. Details for judging formalization

Here is the exact prompt for LLM judging the faithfulness of formalization.

```text
You will receive a math problem consisting of its natural language statement along with its formal statement in LEAN 4.

Please evaluate whether the formal LEAN statement appropriately translates the natural language statement based on the following criteria:

1. Key Elements: The problem's essential components are correctly represented in LEAN code.
2. Mathematical Accuracy: The translation preserves the accuracy of the mathematical content.
3. Structural Fidelity: The translation aligns closely with the original problem, maintaining its structure and purpose.
4. Comprehensiveness: All assumptions, conditions, and goals present in the natural language statement are included in the LEAN translation.

Your answer should be in the following format:

Thought: [Your Answer]

Judgement: [Your Answer, one of {Appropriate, Inappropriate}]

---

Following are the example problems label for the reasonability of their translation.

# Example 1:

## Original natural language statement of the problem:

For the graph of a certain quadratic $y = ax^2 + bx + c$, the vertex of the parabola is $(2,10)$, and one of the x-intercepts is $(1,0)$. What is the x-coordinate of the other x-intercept?

## Translated formal statement:
```lean
theorem quadratic_other_intercept
    (f :$\mathbb{R}$→$\mathbb{R}$) 
    (a b c :$\mathbb{R}$)
    (h_quad : ∀ x, f x = a * x^2 + b * x + c)
    (h_vertex : f 2 = 10 ∧ ∀ x, f x ≤ f 2)  -- vertex at (2,10)
    (h_intercept1 : f 1 = 0) :               -- x-intercept at (1,0)
    f 3 = 0 := by                            -- other x-intercept at (3,0)
```

Thought: The Lean translation of the problem is appropriate because it accurately captures the intent and reasoning of the original problem. The problem's key elements, such as the vertex, axis of symmetry, and x-intercepts of the quadratic function, are correctly translated into Lean code. The logical flow of the proof mirrors the original reasoning, starting with the symmetry property of the quadratic function and using it to determine the second x-intercept. The mathematical accuracy is preserved, as the proof correctly applies the vertex property and symmetry to arrive at the solution. Furthermore, the translation aligns well with the original problem in natural language, maintaining fidelity to its structure and purpose. Overall, the translation is both faithful and complete, making it an appropriate representation of the original problem.

Judgement: Appropriate

# Example 2:

## Original natural language statement of the problem:

Draw a tangent line from the point$(4, 3) $to the circle$(x-2)^2 + (y-1)^2 = 1$. What is the equation of the line that passes through the two tangent points?

## Translated formal statement:
```lean
theorem tangent_line_equation (x y :$\mathbb{R}$) :
  let P :$\mathbb{R}$×$\mathbb{R}$:= (4, 3)  -- Point P
  let C :$\mathbb{R}$×$\mathbb{R}$:= (2, 1)  -- Center of first circle
  let r :$\mathbb{R}$:= 1          -- Radius of first circle
  -- Points (x,y) satisfying both circle equations
  let on_first_circle := (x - 2)^2 + (y - 1)^2 = 1
  let on_second_circle := (x - 3)^2 + (y - 2)^2 = 2
  -- If point is on both circles
  on_first_circle ∧ on_second_circle →
  -- Then it lies on the line 2x + 2y - 7 = 0
  2*x + 2*y - 7 = 0 := by
```

Thought: The Lean translation of the problem is inappropriate because it fundamentally changes the intent of the original problem. The original problem asks to derive the equation of the tangent line passing through the intersection points of two circles, but the translation assumes the equation (2x + 2y - 7 = 0) is already given and instead asks to prove that the intersection points lie on this line. This shifts the problem from a construction task to a verification task, losing the original problem's focus on deriving the result through geometric and algebraic reasoning. Additionally, the translation omits the key reasoning step of subtracting the circle equations to derive the line equation, which is central to the original problem. As a result, the translation fails to accurately represent the problem's intent and educational value, making it an incomplete and inappropriate representation.

Judgement: Inappropriate

Example3:

## Original natural language statement of the problem:

If$a,b,c,d > 0$and$abcd = 1$, prove that \n\n$\frac{1}{a+b+c +1}+ \frac{1}{b+c+d+1}+\frac{1}{c+d+a+1}+\frac{1}{d+a+b+1} \le\frac{1}{a+3} +\frac{1}{b+3} + \frac{1}{c+3} + \frac{1}{d+3}$\n\n -/

## Translated formal statement:
```lean4
theorem lean_workbook_49553 (a b c d :$\mathbb{R}$) (habc : a * b * c * d = 1) : (1 / (a + b + c + 1) + 1 / (b + c + d + 1) + 1 / (c + d + a + 1) + 1 / (d + a + b + 1)) ≤ (1 / (a + 3) + 1 / (b + 3) + 1 / (c + 3) + 1 / (d + 3))  :=  by sorry
```

Thought: The Lean translation of the problem is inappropriate because the condition$a,b,c,d>0$is ignored in the formal statement.

Judgement: Inappropriate

Example4:

## Original natural language statement of the problem:

If$a=b=c=2$so$\sum_{cyc}\frac{(a-1)^2}{a^2+2}=\frac{1}{2}$. We'll prove that$\frac{1}{2}$is the answer.

## Translated formal statement:
```lean4
theorem lean_workbook_plus_1478 (a b c :$\mathbb{R}$) (ha : a = 2) (hb : b = 2) (hc : c = 2) : (a - 1) ^ 2 / (a ^ 2 + 2) + (b - 1) ^ 2 / (b ^ 2 + 2) + (c - 1) ^ 2 / (c ^ 2 + 2) = 1 / 2   :=  by sorry
```

Thought: The Lean translation of the problem is appropriate because it accurately captures the assumptions and the goal in the natural language statement.

Judgement: Appropriate

## Original natural language statement of the problem:

{informal_statement}

## Translated formal statement:
```lean4
{formal_statement}
```
```

## Appendix D. Details for Informal-Based Scaffolded Data Synthesis

In this section, we provide all the details for informal-based scaffolded data synthesis. We start with the prompt template for different LLM queries, including the prompt template to generate harder variant, simpler/sub-problem, as well as the prompt template to judge the simplicity and correctness. Then, we provide more details for the synthesis pipeline.

**Prompt template for solving the original problem.** Note that for both input with natural language problem and statement written in Lean, we use the same prompt because we find that general-purpose models can understand the Lean although they cannot generate the whole proof correctly.

```text
Solve the following math problem probably written in Lean 4:
{problem}
Provide a detailed solution. Note that you don't need to prove the problem in Lean 4, just provide a detailed solution in natural language or math notation.
```

**Prompt template for generating sub/simpler problems.**

```text
I will give you a math problem along with its full solution.
Your task is to generate simpler problems which may enable a student to build up the skills to solve the given problem. Each simpler problem should reflect the idea of a core step in the solution.
Each generated problem must:
(1) Be completely self-contained and standalone: it should be clearly stated as an independent question that someone could read and work on without seeing the original problem and the other generated problems.
(2) Be purely proof-based: stated explicitly as a question of the form "Prove that...".
(3) Be related to the core steps in the solution of the original problem or reflect the core idea, and should not be just trivial and straightforward derivation, plug-in calculation, or solving simple equations.
After generating the problems, carefully evaluate your own output and perform the following checks:
(1) Ensure each problem is fully self-contained: check that it does not rely on undefined variables, terms, or concepts from the original problem or other problems.
(2) Ensure the set of problems is diverse, covering different steps or aspects of the original reasoning.
(3) Ensure that the problems are not too simple or trivial, and that they require a meaningful proof. Avoid trivial and straightforward derivation, plug-in calculation, or solving simple equations.
Wrap each final selected problem between the tags <newproblem> and </newproblem> to make it easy to extract.  
Do not include anything else. 
Problem: {problem}
Solution: {solution}
```

**Prompt template for generating harder variant of problems.**

```text
I now have a math problem and its solution at hand, and I would like you to modify the problem to generate a diverse set of new problems based on it. Below is the problem (probably written in Lean 4):
---
{problem}
---

Below is the solution:
---
{solution}
---

Now I would like you to generate at least 10 new math problems, each clearly different from the original. For example, you can make the following modifications to make the problem different:
Change the number in the original problem to generate a new problem.
Transform the algebraric formula such that it needs more simplification. For example, change cos(x) in the original problem into 1 - 2 sin(x/2)^2.
Add more terms in the inquality. For example, the original problem need to show that f(x) is non-negative, you transform the problem into f(x) + (1/x - a)^2 is non-negative, or f(x) \cdot (x - b)^2 is non-negative.
Lift a real variable into a complex number, a vector or even matrix. For example previously when solving quadratic equation in real space, you now change it to complex field, which might lead to slightly different solutions. Or the original problem considers planer geometry, you modify the problem into 3 dimensional geometry.
Substitute a variable into a more complex algebraic form, which includes but not limited to changeing a variable into a polynomial, exponential, logarithm, or even trigonometry. For example, you change variable x in the original problem into y^2 or even y^n in the new problem. Another example is that you are given some condition like a + b + c = 1 where a, b, c are all positive, and you need to show f(a, b, c) is non-negative. You can change the condition into x y + y z + z x = x y z with x, y, z positive, which is equivalent to 1/x + 1/y + 1/z = 1, and modify the statement to show that f(1/x, 1/y, 1/z) is non-negative.
Use the conclusion in the problem as a step to solve another problem. For example, the original problem need to solve equation f(x) = 0, where x is the variable. Now you change it to solve f( exp(x) ) = 0, f( ln(x) ) = 0, or f( cos(x) ) = 0, or even f( x^2 ) = 0.
These are just some example transformations you can try, and you are not limited to these transformations. Do not overcomplicate the problem, and it is acceptable to make simple transformations. The new math problem should not be simpler than the example problems I provided, i.e., you should not simplify the original problem and make it the new problem.
Each generated problem must:
(1) Be purely proof-based: stated explicitly as a question of the form "Prove that…". Do not generate problems that ask to "determine", "compute", "evaluate", "find", or similar.
(2) Be completely self-contained and standalone.
(3) Be mathematically valid and solvable.
(4) Be meaningfully different to ensure diversity.
Wrap each generated problem between the tags <newproblem> and </newproblem> to make it easy to extract.
Do not include anything else.
```

**Prompt template to judge the correctness and simplicity of the synthesized statement.**

```text
I will give you a math problem written in Lean 4, and I will ask you to determine (1) whether the problem is correct or not; and (2) whether the problem is too simple to prove or disprove in Lean 4. Here is a list of too simple problems in Lean 4: simple calculations, simple algebraic manipulations, solving single variable linear equations (by just a 1-step calculation), and inequalities proved by an easy sum-of-squares technique. However, do not include inequality proving with the square root since that might be more complex. Please do not label other problems, such as other more complex inequalities, limits, and integrals, as simple problems. Also, please do not label problems that deals with integers (more related to number theory), higher order roots, complex numbers, matrices, polynomials, group, finite-sum, or functional equations (e.g. Let$f$be a function such that$$f(x^2) = xf(x) $$for all$$x \\in R.$$Prove that$f$is an odd function, i.e., $$f(-x) = -f(x) $$for all$$x \\in R$$.), since these problems might shed lights on other hard problems.
Please carefully analyze the problem and provide an explanation of your reasoning.
For judging the correctness, if the problem is correct, respond with "yes"; if the problem is incorrect, respond with "no"; if you are not sure, respond with "unsure".
For judging the simplicity, if the problem is too simple to prove or disprove in Lean 4, respond with "yes"; if the problem is not too simple, respond with "no"; if you are not sure, respond with "unsure". Please refer to the list of too-simple problems I provided.
Wrap your judgment between <judge> and </judge> to make it easy to extract. You should first answer yes or no to the correctness and then answer yes or no to the simplicity, separated by a comma. For example, if you think the problem is correct and not too simple, you should respond with <judge>yes, no</judge>. If you are not sure about the correctness, you can respond with <judge>unsure, no</judge> because at least this problem is not simple. If you think the problem is incorrect but it is not too easy to disprove, you can respond with <judge>no, no</judge>. If you think the problem is correct but too simple to prove or disprove, you can respond with <judge>yes, yes</judge>. Again, when judging the simplicity, please do not label inequality proving with the square root as simple, since that might be more complex than expected. Please do not include problems such as more complex inequalities, or problems that include limits, and integrals. Also, please do not label problems that deals with integers (more related to number theory), higher order roots, complex numbers, matrices, polynomials, group, finite-sum, or functional equations, as simple problems, since these notions are more likely to related to hard problems and proving problems related to these concepts in Lean 4 might not be easy even if the problem is straight-forward in natural language.
Do not include anything else. 
Problem: {formal_statement}
```

**More details for informal-based scaffolded data synthesis.** In the following part, we discuss some of the design choices and details for the scaffolded data synthesis pipeline.

1. **(Informal statements generation.)** For the informal statements generation, one design choice is whether to generate multiple questions during the same inference, or to generate multiple times while only generating 1 (or very few) questions during one inference. From our experiment, we observe that for the Qwen3-32B model, generating multiple questions during the same inference is better, since the generated questions are likely to be different. Otherwise, there might be very similar questions among different generations. Besides, we also find that repeatedly generating a single problem multiple times doesn't significantly increase the number of different problems. Thus, for efficiency considerations, we only query the LLM (Qwen3-32B) once for generating multiple hard variants/simple problems of a given math problem.
2. **(Formalization and quality checking.)** For each generated informal statement, we call the trained formalizer to formalize the problem twice. Then, we then query Qwen3-8B for 3 times for each formalization, and decide if the formalization is aligned with the informal statement using majority voting (among three queries). We decide to formalize each informal statement twice in order to balance the efficiency and the number of generated problems. We only keep at most one formalization for each generated informal statement.
3. **(Negation and difficulty filtering.)** For each formalization, we query Qwen3-32B 4 times, where each inference judges the correctness and the simplicity simultaneously. The final correctness is judged by strict majority voting among the 4 judges, while the final simplicity is determined if all 4 judges think the problem is easy. We use such strict criteria to minimize the probability of discarding hard and valuable problems. The judging is still efficient because Qwen3-32B often enters the "fast thinking" mode (no long chain-of-thought for reasoning).
4. **(Final deduplication.)** At the end of the pipeline, we filter out duplicated statements under exact match.

## Appendix E. RL Training Details

We further explain our RL training in detail.

### E.1 RL Implementation

![Figure 9: Illustrative figure for our multi-task RL. This pipeline improves models' performance on both the whole proof and the self-correction at the same time without additional design on the framework or algorithm.](images/008_rl_illustration.png)

[Figure 9](images/008_rl_illustration.png): Illustrative figure for our multi-task RL. This pipeline improves models' performance on both the whole proof and the self-correction at the same time without additional design on the framework or algorithm.

We begin by collecting 50K challenging statements and 50K self‑correction samples. Each self‑correction sample contains a statement, an output generated by the SFT model, and an associated error message. During RL training, we consumed approximately 46K and 64K unique inputs for the 8B and 32B models on 1 epoch, respectively.

We use the VeRL framework with several key setups: We adopt a batch size of 128 and n of 8 for parallel rollouts and reward function calls (via the Lean compiler), while using a mini-batch size of 32, accepting a certain degree of off-policy training in exchange for a higher frequency of policy optimization.

For the dynamic sampling, we set the over-sample batch size equal to three times of train batch size and filter out inputs with a pass rate equal to 0 or higher than 0.75.

We use a maximum prompt length of 16K for those long inputs in the self-correction task. We set the maximum response length to 24K to support a reasonably sized reasoning trajectory while staying within the Qwen3 model’s native 40K context window, ensuring generation quality. We enable the overlong penalty with a 4K overlong buffer and set the overlong penalty factor to 1.

We use token-averaged policy loss and do not use KL divergence or entropy terms in our training objective.

![Figure 10: Training curves comparing 32B and 8B models: training rewards (top row) and generation lengths (bottom row) for whole proof completion and self-correction tasks. Note that the rewards are averaged over all generated rollouts, including those then filtered by the dynamic sampling strategy, thus reflecting the policy improvement.](images/009_rl_completion_training_reward_32B_8B.png)

![Figure 10: Training curves comparing 32B and 8B models: training rewards (top row) and generation lengths (bottom row) for whole proof completion and self-correction tasks. Note that the rewards are averaged over all generated rollouts, including those then filtered by the dynamic sampling strategy, thus reflecting the policy improvement.](images/010_rl_revision_training_reward_32B_8B.png)

![Figure 10: Training curves comparing 32B and 8B models: training rewards (top row) and generation lengths (bottom row) for whole proof completion and self-correction tasks. Note that the rewards are averaged over all generated rollouts, including those then filtered by the dynamic sampling strategy, thus reflecting the policy improvement.](images/011_rl_completion_length_32B_8B.png)

![Figure 10: Training curves comparing 32B and 8B models: training rewards (top row) and generation lengths (bottom row) for whole proof completion and self-correction tasks. Note that the rewards are averaged over all generated rollouts, including those then filtered by the dynamic sampling strategy, thus reflecting the policy improvement.](images/012_rl_revision_length_32B_8B.png)

[Figure 10](images/009_rl_completion_training_reward_32B_8B.png): Training curves comparing 32B and 8B models: training rewards (top row) and generation lengths (bottom row) for whole proof completion and self-correction tasks. Note that the rewards are averaged over all generated rollouts, including those then filtered by the dynamic sampling strategy, thus reflecting the policy improvement.

### E.2 Further Discussion on RL

For the design of training with self-correction, we explored both tool-use and multi-turn reinforcement learning.

However, the former not only requires a dedicated tool-calling design, but also demands strong model capability to follow the tool-calling protocol, especially in complex and lengthy formal language scenarios like Lean, which is particularly challenging for our relatively small model.

Meanwhile, multi-turn approaches introduce various engineering challenges, especially on the rollout engine side, such as asynchronous generation.

Moreover, on the algorithm side, the effectiveness and efficiency of multi-turn RL still require further validation.

We have explored some preliminary approaches, but they remain immature.

Therefore, we adopt a more straightforward implementation that is natively integrated into the current RL framework, as illustrated in Figure 9.

In this setup, we have two different types of inputs for RL and detailed training curves are shown in Figure 10.

Furthermore, for the algorithmic design, like the advantage estimator, we explored different popular GRPO variants but did not observe significant differences.
