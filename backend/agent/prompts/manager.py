from pathlib import Path

import jinja2


class PromptManager:
    """
    Centrally manages system and instruction prompt templates stored as Markdown files.
    Uses Jinja2 to render dynamic parameters and support template inclusion (e.g., UI Guide).
    """
    def __init__(self, prompts_dir: Path | None = None):
        if prompts_dir is None:
            self.prompts_dir = Path(__file__).parent
        else:
            self.prompts_dir = Path(prompts_dir)

        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self.prompts_dir)),
            autoescape=False
        )

    def get_prompt(self, template_name: str, **kwargs) -> str:
        template = self.env.get_template(template_name)
        return template.render(**kwargs).strip()
