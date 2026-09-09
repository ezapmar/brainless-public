---
name: pollinate
description: Cross-reference a new note with your existing Beliefs and Ideas to find resonance or dissonance.
argument-hint: <path/to/note>
---

## Inputs
- A path to a markdown file (e.g., `Library/Books/The-SaaS-Playbook.md`)
- The current vault's `Thinking/Beliefs/` and `.wiki/ideas/` folders.

## Reads
- The target file provided in the argument.
- `Thinking/Beliefs/*.md` (all files)
- `.wiki/ideas/*.md` (all files)

## Behavior
1. **Analyze the Target**: Identify the 3 core "theses" or "claims" in the target note.
2. **Cross-Reference Beliefs**:
    - **Resonance**: Find 1-2 beliefs that are *reinforced* by this new information. Explain exactly how.
    - **Dissonance**: Find 1-2 beliefs that are *challenged* or *contradicted* by this new information. This is critical for avoiding echo chambers.
3. **Cross-Reference Ideas**:
    - Identify if this new info validates or invalidates any existing `#status/seed` ideas.
4. **Generate Mutations**: Propose 2 new "Seed" ideas (`#type/idea`) that result from the intersection of the target note and your current beliefs.

## Output format
# 🧠 Pollination Report: [[Note Name]]

## 🟢 Resonance (Reinforced Beliefs)
- **[[Belief Name]]**: <how it supports it>

## 🔴 Dissonance (Challenged Beliefs)
- **[[Belief Name]]**: <how it challenges it - why this matters>

## 💡 New Mutations (Proposed Seed Ideas)
1. **[[New Idea Title]]**: <the core concept derived from the intersection>
2. **[[New Idea Title]]**: <the core concept derived from the intersection>

## 📝 Synthesis Reflection
<A 3-sentence summary of how this new information changes the "landscape" of your thinking.>

---
Follow `.wiki/_commands/_shared-rules.md`.
