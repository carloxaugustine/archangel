# Resume rules

`build_resume.py` enforces these. Edit `[rules]` in `facts.toml` to loosen or tighten them.

## Layout
- Jake's LaTeX template, one page, one column, text readable by applicant tracking systems.
- Header: name, then phone, email, LinkedIn, website. No street address.
- Sections: Education, Experience, Projects, Skills.

## Bullets
- Lead with a strong verb. No pronouns, no periods, digits instead of words.
- Use result, measure, method when a measured result exists. For in-progress work or fixes, state the problem and what was done. Never invent a result to fit the format.
- Plain, specific, checkable. Spell out an acronym the first time by listing it under `[acronyms]`.

## Facts
- Every bullet comes from the person's own documents or from what they confirmed in chat.
- Rewording may add keywords only where the source supports them. A keyword with no supporting fact stays out.
- Put two wordings of one fact in `[rules] variants`, so a resume never says the same thing twice.

## Tailoring
- Order entries and bullets so the posting's required skills come first. Pick skills rows that match.
- Specs live in `applications/<id>-resume-spec.json`. Build with `./resume-builder/build_resume.py <spec>` and upload only the PDF it prints.
