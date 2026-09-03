from pathlib import Path

path = Path('.github/workflows/bootstrap-issue-100-publisher.yml')
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    text = text.replace(old, new, 1)


old_modes = "if (pair !~ /^(000000->100644|100644->100644|100755->100755|100644->000000|100755->000000)$/) exit 1"
new_modes = "if (pair !~ /^(000000->100644|100644->100644|100755->100755|100644->000000)$/) exit 1"
if text.count(old_modes) != 2:
    raise SystemExit(f'mode-pairs: expected two matches, found {text.count(old_modes)}')
text = text.replace(old_modes, new_modes)

replace_once(
    '          branch="agent/issue-100-${SPEC_HASH:0:12}"\n',
    '          branch="agent/issue-100-${SPEC_HASH:0:12}-${BASE_SHA:0:8}-${sealed_tree:0:12}"\n',
    'artifact-bound branch',
)

replace_once(
'''          same_repo_open_prs() {
            gh api "repos/$GH_REPO/pulls?state=open&per_page=100" |
              jq --arg branch "$branch" --arg repo "$GH_REPO" \\
                '[.[] | select(.head.ref == $branch and .head.repo.full_name == $repo)]'
          }
''',
'''          same_repo_open_prs() {
            gh api --paginate --slurp -X GET "repos/$GH_REPO/pulls" \\
              -f state=open -f per_page=100 -f head="$owner:$branch" |
              jq --arg branch "$branch" --arg repo "$GH_REPO" \\
                '[.[][] | select(.head.ref == $branch and .head.repo.full_name == $repo)]'
          }
''',
    'paginated same-repo PR lookup',
)

replace_once(
'''          verify_pr() {
            local json="$1"
            local expected_head="$2"
            test "$(jq 'length' <<<"$json")" -eq 1
            test "$(jq -r '.[0].head.repo.full_name' <<<"$json")" = "$GH_REPO"
            test "$(jq -r '.[0].head.sha' <<<"$json")" = "$expected_head"
            test "$(jq -r '.[0].base.ref' <<<"$json")" = main
            test "$(jq -r '.[0].draft' <<<"$json")" = true
            local body
            body="$(jq -r '.[0].body // ""' <<<"$json")"
            test "$(grep -Ec '^[[:space:]]*- Agent-Issue: #100[[:space:]]*$' <<<"$body" || true)" -eq 1
            test "$(grep -Eic 'Agent-Issue:' <<<"$body" || true)" -eq 1
            test "$(grep -Ec "^<!-- agent-bootstrap-publish:v2 spec=$SPEC_HASH base=$BASE_SHA tree=[0-9a-f]{40} -->$" <<<"$body" || true)" -eq 1
            local parent tree
            parent="$(gh api "repos/$GH_REPO/commits/$expected_head" --jq '.parents[0].sha')"
            tree="$(gh api "repos/$GH_REPO/git/commits/$expected_head" --jq '.tree.sha')"
            test "$parent" = "$BASE_SHA"
            grep -Eq "^<!-- agent-bootstrap-publish:v2 spec=$SPEC_HASH base=$BASE_SHA tree=$tree -->$" <<<"$body"
          }
''',
'''          verify_pr() {
            local json="$1"
            local expected_head="$2"
            test "$(jq 'length' <<<"$json")" -eq 1 || return 1
            test "$(jq -r '.[0].head.repo.full_name' <<<"$json")" = "$GH_REPO" || return 1
            test "$(jq -r '.[0].head.sha' <<<"$json")" = "$expected_head" || return 1
            test "$(jq -r '.[0].base.ref' <<<"$json")" = main || return 1
            test "$(jq -r '.[0].draft' <<<"$json")" = true || return 1
            local body
            body="$(jq -r '.[0].body // ""' <<<"$json")" || return 1
            test "$(grep -Ec '^[[:space:]]*- Agent-Issue: #100[[:space:]]*$' <<<"$body" || true)" -eq 1 || return 1
            test "$(grep -Eic 'Agent-Issue:' <<<"$body" || true)" -eq 1 || return 1
            test "$(grep -Ec "^<!-- agent-bootstrap-publish:v2 spec=$SPEC_HASH base=$BASE_SHA tree=[0-9a-f]{40} -->$" <<<"$body" || true)" -eq 1 || return 1
            local parent tree
            parent="$(gh api "repos/$GH_REPO/commits/$expected_head" --jq '.parents[0].sha')" || return 1
            tree="$(gh api "repos/$GH_REPO/git/commits/$expected_head" --jq '.tree.sha')" || return 1
            test "$parent" = "$BASE_SHA" || return 1
            grep -Eq "^<!-- agent-bootstrap-publish:v2 spec=$SPEC_HASH base=$BASE_SHA tree=$tree -->$" <<<"$body" || return 1
          }
''',
    'verify_pr failure propagation',
)

replace_once(
'''          if test "$(jq 'length' <<<"$prs")" -eq 0; then
            remote_sha="$(gh api "repos/$GH_REPO/git/ref/heads/$branch" --jq '.object.sha' 2>/dev/null || true)"
            if test -n "$remote_sha"; then
              gh api -X DELETE "repos/$GH_REPO/git/refs/heads/$branch"
            fi

            git remote set-url origin "https://github.com/${GH_REPO}.git"
            gh auth setup-git
            git push origin "HEAD:refs/heads/$branch"
            created_branch=1
            canonical_sha="$sealed_sha"

            body_file="$RUNNER_TEMP/pr-body.md"
            cat > "$body_file" <<EOF
          Implements the complete authorized governance remediation in Issue #100.

          This PR is the one-time bootstrap candidate generated without giving Codex GitHub write credentials. The candidate was generated read-only, validated and sealed without model/write credentials, then published only by this trusted step.

          - Agent-Issue: #100
          <!-- agent-bootstrap-publish:v2 spec=$SPEC_HASH base=$BASE_SHA tree=$sealed_tree -->
          EOF
            gh pr create --repo "$GH_REPO" --draft --base main --head "$branch" \\
              --title 'Pipeline v2: one Issue approval to autonomous verified merge' \\
              --body-file "$body_file"

            prs="$(same_repo_open_prs)"
            test "$(jq 'length' <<<"$prs")" -eq 1
            created_pr="$(jq -r '.[0].number' <<<"$prs")"
            verify_pr "$prs" "$canonical_sha"
          fi
''',
'''          if test "$(jq 'length' <<<"$prs")" -eq 0; then
            remote_sha="$(gh api "repos/$GH_REPO/git/ref/heads/$branch" --jq '.object.sha' 2>/dev/null || true)"
            if test -n "$remote_sha"; then
              remote_parent="$(gh api "repos/$GH_REPO/commits/$remote_sha" --jq '.parents[0].sha')"
              remote_tree="$(gh api "repos/$GH_REPO/git/commits/$remote_sha" --jq '.tree.sha')"
              if test "$remote_parent" != "$BASE_SHA" || test "$remote_tree" != "$sealed_tree"; then
                echo "::error::Artifact-bound orphan branch does not match the current sealed candidate."
                exit 1
              fi
              canonical_sha="$remote_sha"
            else
              git remote set-url origin "https://github.com/${GH_REPO}.git"
              gh auth setup-git
              git push origin "HEAD:refs/heads/$branch"
              created_branch=1
              canonical_sha="$sealed_sha"
            fi

            body_file="$RUNNER_TEMP/pr-body.md"
            cat > "$body_file" <<EOF
          Implements the complete authorized governance remediation in Issue #100.

          This PR is the one-time bootstrap candidate generated without giving Codex GitHub write credentials. The candidate was generated read-only, validated and sealed without model/write credentials, then published only by this trusted step.

          - Agent-Issue: #100
          <!-- agent-bootstrap-publish:v2 spec=$SPEC_HASH base=$BASE_SHA tree=$sealed_tree -->
          EOF
            gh pr create --repo "$GH_REPO" --draft --base main --head "$branch" \\
              --title 'Pipeline v2: one Issue approval to autonomous verified merge' \\
              --body-file "$body_file"

            prs="$(same_repo_open_prs)"
            test "$(jq 'length' <<<"$prs")" -eq 1
            created_pr="$(jq -r '.[0].number' <<<"$prs")"
            verify_pr "$prs" "$canonical_sha"
          fi
''',
    'artifact-bound orphan recovery',
)

replace_once(
'''          if ! (
            remote_sha="$(gh api "repos/$GH_REPO/git/ref/heads/$branch" --jq '.object.sha')"
            test "$remote_sha" = "$canonical_sha"
            prs="$(same_repo_open_prs)"
            verify_pr "$prs" "$canonical_sha"
            fresh_base="$(gh api "repos/$GH_REPO/branches/main" --jq '.commit.sha')"
            test "$fresh_base" = "$BASE_SHA"
            issue_json="$(gh api "repos/$GH_REPO/issues/100")"
            test "$(jq -r .state <<<"$issue_json")" = open
            test "$(jq '[.labels[].name | select(startswith("type:"))] | length' <<<"$issue_json")" -eq 1
            test "$(jq -r '[.labels[].name | select(startswith("type:"))][0]' <<<"$issue_json")" = type:implementation
            test "$(jq '[.labels[].name | select(startswith("agent:"))] | length' <<<"$issue_json")" -eq 1
            test "$(jq -r '[.labels[].name | select(startswith("agent:"))][0]' <<<"$issue_json")" = agent:running
            fresh_hash="$(
              printf '%s' "$issue_json" | node -e '
                const crypto=require("crypto"),fs=require("fs");
                const issue=JSON.parse(fs.readFileSync(0,"utf8"));
                process.stdout.write(
                  crypto.createHash("sha256").update(`${issue.title}\\n${issue.body || ""}`,"utf8").digest("hex")
                );
              '
            )"
            test "$fresh_hash" = "$SPEC_HASH"
          ); then
            echo "::error::Publication postcondition changed; cleaning objects created by this run."
            cleanup_current
            exit 1
          fi
''',
'''          verify_postconditions() {
            local remote_sha prs fresh_base issue_json fresh_hash
            remote_sha="$(gh api "repos/$GH_REPO/git/ref/heads/$branch" --jq '.object.sha')" || return 1
            test "$remote_sha" = "$canonical_sha" || return 1
            prs="$(same_repo_open_prs)" || return 1
            verify_pr "$prs" "$canonical_sha" || return 1
            fresh_base="$(gh api "repos/$GH_REPO/branches/main" --jq '.commit.sha')" || return 1
            test "$fresh_base" = "$BASE_SHA" || return 1
            issue_json="$(gh api "repos/$GH_REPO/issues/100")" || return 1
            test "$(jq -r .state <<<"$issue_json")" = open || return 1
            test "$(jq '[.labels[].name | select(startswith("type:"))] | length' <<<"$issue_json")" -eq 1 || return 1
            test "$(jq -r '[.labels[].name | select(startswith("type:"))][0]' <<<"$issue_json")" = type:implementation || return 1
            test "$(jq '[.labels[].name | select(startswith("agent:"))] | length' <<<"$issue_json")" -eq 1 || return 1
            test "$(jq -r '[.labels[].name | select(startswith("agent:"))][0]' <<<"$issue_json")" = agent:running || return 1
            fresh_hash="$(
              printf '%s' "$issue_json" | node -e '
                const crypto=require("crypto"),fs=require("fs");
                const issue=JSON.parse(fs.readFileSync(0,"utf8"));
                process.stdout.write(
                  crypto.createHash("sha256").update(`${issue.title}\\n${issue.body || ""}`,"utf8").digest("hex")
                );
              '
            )" || return 1
            test "$fresh_hash" = "$SPEC_HASH" || return 1
          }

          if ! verify_postconditions; then
            echo "::error::Publication postcondition changed; cleaning objects created by this run."
            cleanup_current
            exit 1
          fi
''',
    'postcondition failure propagation',
)

path.write_text(text, encoding='utf-8')
print('bootstrap review fixes applied')
