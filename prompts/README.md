# Seed prompts

YAML seeds for the MLflow prompt registry: one file per task (`<task>.yaml`: instructions, answer schema ref, evidence policy) and one per agent role (`agent.<role>.yaml`). `amber register-prompts` pushes them; code loads by version.
