FROM pragent/pr-agent@sha256:f9b562fdd2ec5cbbcfc25d629ceb1df8d2431cb716640205747c558d5aef080c

COPY patches/apply_piparo_patches.py /tmp/apply_piparo_patches.py
RUN python /tmp/apply_piparo_patches.py \
  && python -m py_compile \
    /app/pr_agent/tools/pr_code_suggestions.py \
    /app/pr_agent/tools/pr_reviewer.py \
    /app/pr_agent/tools/pr_description.py \
  && rm /tmp/apply_piparo_patches.py
