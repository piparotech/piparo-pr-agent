from pathlib import Path

SUGGESTIONS = Path("/app/pr_agent/tools/pr_code_suggestions.py")
REVIEWER = Path("/app/pr_agent/tools/pr_reviewer.py")

SUMMARY_NOTE = (
    "Nice work — the code is already in good shape. "
    "The notes below are small optional refinements, so I’m keeping them in one place "
    "instead of spreading them across the diff."
)

COMMAND_HINT = (
    "\n\n---\n\n"
    "💬 **Need another pass?** Comment `@piparo-agent /review`, "
    "`@piparo-agent /improve`, `@piparo-agent /describe`, "
    "or `@piparo-agent /ask <question>`."
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"Patch target not found: {label}")
    return text.replace(old, new, 1)


def patch_suggestions() -> None:
    text = SUGGESTIONS.read_text()

    old_progress = '''            # publish "Preparing suggestions..." comments
            if (get_settings().config.publish_output and get_settings().config.publish_output_progress and
                    not get_settings().config.get('is_auto_command', False)):
                if self.git_provider.is_supported("gfm_markdown"):
                    self.progress_response = self.git_provider.publish_comment(self.progress)
                else:
                    self.git_provider.publish_comment("Preparing suggestions...", is_temporary=True)
'''
    new_progress = '''            # Publish or update a persistent "in progress" suggestions comment.
            if get_settings().config.publish_output and get_settings().config.publish_output_progress:
                progress_body = (
                    "## PR Code Suggestions ✨\\n\\n"
                    "⏳ Looking through the changed code now. I’ll update this comment with suggestions when I’m done."
                )
                if self.git_provider.is_supported("gfm_markdown"):
                    self.progress_response = self._publish_or_update_progress_comment(
                        "## PR Code Suggestions ✨", progress_body
                    )
                else:
                    self.progress_response = self.git_provider.publish_comment(
                        "Preparing suggestions...", is_temporary=False
                    )
'''
    text = replace_once(text, old_progress, new_progress, "suggestions progress comment")

    old_method_anchor = '''    async def add_self_review_text(self, pr_body):
'''
    helper_method = '''    def _publish_or_update_progress_comment(self, initial_header: str, body: str):
        try:
            for comment in self.git_provider.get_issue_comments():
                if comment.body.startswith(initial_header):
                    self.git_provider.edit_comment(comment, body)
                    return comment
        except Exception as e:
            get_logger().exception(f"Failed to update progress comment, error: {e}")
        return self.git_provider.publish_comment(body)

'''
    text = replace_once(text, old_method_anchor, helper_method + old_method_anchor, "suggestions progress helper")

    old_empty_method = '        pr_body = "## PR Code Suggestions ✨\\n\\nNo code suggestions found for the PR."'
    new_empty_method = (
        f'        pr_body = "## PR Code Suggestions ✨\\n\\n{SUMMARY_NOTE}'
        '\\n\\nNo code suggestions found this time."'
    )
    text = replace_once(text, old_empty_method, new_empty_method, "suggestions empty output")

    old_summary = '''            pr_body = "## PR Code Suggestions ✨\\n\\n"

            if len(data.get('code_suggestions', [])) == 0:
                pr_body += "No suggestions found to improve this PR."
                return pr_body

            if get_settings().config.is_auto_command:
                pr_body += "Explore these optional code suggestions:\\n\\n"
'''
    new_summary = f'''            pr_body = "## PR Code Suggestions ✨\\n\\n"
            pr_body += "{SUMMARY_NOTE}\\n\\n"

            if len(data.get('code_suggestions', [])) == 0:
                pr_body += "No code suggestions found this time."
                return pr_body

            if get_settings().config.is_auto_command:
                pass
'''
    text = replace_once(text, old_summary, new_summary, "suggestions summary note")

    SUGGESTIONS.write_text(text)


def patch_reviewer() -> None:
    text = REVIEWER.read_text()

    old_progress = '''            if get_settings().config.publish_output and not get_settings().config.get('is_auto_command', False):
                self.git_provider.publish_comment("Preparing review...", is_temporary=True)
'''
    new_progress = '''            progress_response = None
            review_header = f"{PRReviewHeader.REGULAR.value} 🔍"
            if (get_settings().config.publish_output and get_settings().config.publish_output_progress
                    and get_settings().pr_reviewer.persistent_comment and not self.incremental.is_incremental):
                progress_response = self._publish_or_update_progress_comment(
                    review_header,
                    f"{review_header}\\n\\n⏳ Reviewing this PR now. I’ll update this comment with the full review when I’m done."
                )
            elif get_settings().config.publish_output and not get_settings().config.get('is_auto_command', False):
                self.git_provider.publish_comment("Preparing review...", is_temporary=True)
'''
    text = replace_once(text, old_progress, new_progress, "review progress comment")

    old_should_publish = '''            if not should_publish:
                reason = "Review output is not published"
                if get_settings().config.publish_output:
                    reason += ": no major issues detected."
                get_logger().info(reason)
                get_settings().data = {"artifact": pr_review}
                return
'''
    new_should_publish = '''            if not should_publish:
                reason = "Review output is not published"
                if get_settings().config.publish_output:
                    reason += ": no major issues detected."
                get_logger().info(reason)
                get_settings().data = {"artifact": pr_review}
                if progress_response:
                    self.git_provider.edit_comment(progress_response, pr_review)
                return
'''
    text = replace_once(text, old_should_publish, new_should_publish, "review unpublished output cleanup")

    old_publish = '''            # publish the review
            if get_settings().pr_reviewer.persistent_comment and not self.incremental.is_incremental:
                final_update_message = get_settings().pr_reviewer.final_update_message
                self.git_provider.publish_persistent_comment(pr_review,
                                                            initial_header=f"{PRReviewHeader.REGULAR.value} 🔍",
                                                            update_header=True,
                                                            final_update_message=final_update_message, )
            else:
                self.git_provider.publish_comment(pr_review)
'''
    new_publish = '''            # publish the review
            if get_settings().pr_reviewer.persistent_comment and not self.incremental.is_incremental:
                if progress_response:
                    self.git_provider.edit_comment(progress_response, pr_review)
                else:
                    final_update_message = get_settings().pr_reviewer.final_update_message
                    self.git_provider.publish_persistent_comment(pr_review,
                                                                initial_header=f"{PRReviewHeader.REGULAR.value} 🔍",
                                                                update_header=True,
                                                                final_update_message=final_update_message, )
            else:
                self.git_provider.publish_comment(pr_review)
'''
    text = replace_once(text, old_publish, new_publish, "review final progress update")

    old_helper_anchor = '''    def _should_publish_review_no_suggestions(self, pr_review: str) -> bool:
'''
    helper_method = '''    def _publish_or_update_progress_comment(self, initial_header: str, body: str):
        try:
            for comment in self.git_provider.get_issue_comments():
                if comment.body.startswith(initial_header):
                    self.git_provider.edit_comment(comment, body)
                    return comment
        except Exception as e:
            get_logger().exception(f"Failed to update progress comment, error: {e}")
        return self.git_provider.publish_comment(body)

'''
    text = replace_once(text, old_helper_anchor, helper_method + old_helper_anchor, "review progress helper")

    old_command_hint_anchor = '''        # Output the relevant configurations if enabled
        if get_settings().get('config', {}).get('output_relevant_configurations', False):
'''
    new_command_hint_anchor = f'''        if self.git_provider.is_supported("gfm_markdown"):
            markdown_text += {COMMAND_HINT!r}

        # Output the relevant configurations if enabled
        if get_settings().get('config', {{}}).get('output_relevant_configurations', False):
'''
    text = replace_once(text, old_command_hint_anchor, new_command_hint_anchor, "review command hint")

    REVIEWER.write_text(text)


patch_suggestions()
patch_reviewer()
print("Applied Piparo PR-Agent patches")
