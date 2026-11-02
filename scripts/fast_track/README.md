# Fast-track approval

This automation grants a policy approval for an author's PR when its Jira ticket carries
`ai-fast-track`. It does not perform a code review, merge a PR, enable auto-merge, or
bypass branch rules. Required CI and the merge queue still apply.

## Components

- The consuming repository's `.github/workflows/flow-fast-track.yml` is the trigger: a manual Actions run
  taking a PR number as positive decimal digits without leading zeros. Run it from
  the default branch.
- [`.github/workflows/fast-track.yml`](../../.github/workflows/fast-track.yml) is the shared implementation. It serializes
  requests for the same PR and creates a write token only after validation succeeds.
- `approval.py validate` reads GitHub and Jira and records a decision, including its
  reason, ticket, requester, title, target branch, and head SHA.
- `approval.py approve` revalidates the decision, checks existing App reviews, and
  submits an approval for that SHA. These phases do not depend on event payloads.

Future label or PR-event adapters can call the orchestration after defining their
requester authorization and trusted-ref handling. A trigger is never evidence of
eligibility. No label or automatic PR trigger is enabled here.

## Eligibility

The PR must be open, ready for review, and target the consuming repository's default
branch, `master` for RediSearch or `main` for RediSearchEnterprise. The requester must
be its author and have repository write access. Manual reruns use
`github.triggering_actor`, so rerunning somebody else's request cannot impersonate the
original requester.

The title must use the repository's `[MOD-123] Summary` convention and contain exactly
one distinct ticket key. The title is the authoritative ticket link; body links and
GitHub labels do not establish eligibility. Jira must return that exact ticket key and
its current labels must include `ai-fast-track`. The automation verifies the declared
association, not whether the code actually solves the ticket. Jira permissions
controlling who can set the label are part of the policy's trust boundary.

Before submission, missing or malformed data, permission failures, and unavailable APIs
fail closed. Changing the ticket, head, target, author, or ready/open state during
validation invalidates the request. The approval phase refreshes Jira eligibility and
requester permissions after review pagination, then checks the PR again immediately
before posting. An active policy approval by this App for the same ticket and head is a
no-op; a dismissed or old-head approval does not prevent a new request. The selected
review is fetched again after eligibility validation before returning a no-op.

Each submission carries a unique attempt marker. If the POST response is lost or
malformed, the client searches all review pages for that marker, App identity, and head
SHA and attempts to dismiss matching approvals. It never retries the POST.
Reconciliation is best effort: if GitHub is unavailable or the review is not yet
visible, the run fails with an explicit instruction to inspect PR reviews manually. A
failed run alone does not guarantee that GitHub created no approval.

GitHub's review API accepts a commit SHA but has no atomic conditional approval. The
workflow rechecks after posting and attempts to dismiss its review if the PR changed or
that recheck failed. If dismissal fails, the run fails and requires operator inspection.
Keep the existing stale-review dismissal and latest-push approval rules enabled; a
client-side check cannot replace those protections.

Eligibility is a snapshot at approval time. Removing the Jira label or changing the
title later does not automatically revoke an existing approval. New commits are governed
by the existing stale-review rules and need a new manual request.

## Caller configuration

Each product repository keeps a manual `flow-fast-track.yml` caller. Its reusable job
references `redislabsdev/redisearch-ci-common/.github/workflows/fast-track.yml@v1` and
passes `pr_number`, `ci-common-ref: v1`, and the three secrets below. The
`ci-common-ref` must match the workflow ref so the policy script and orchestration come
from the same release. Neither ref is a dispatch input.

The shared workflow uses the caller's `github.repository`, default branch, requester,
App configuration, and credentials. `DEFAULT_BRANCH` is supplied by the trusted workflow
context, never by the requested PR. The checkout contains only this repository's policy
script at the caller's configured release ref; the PR's code is never checked out or
executed.

## GitHub and Jira setup

1. Create a dedicated GitHub App and install it on each consuming repository, with
   **Pull requests: Read and write** and the mandatory **Metadata: Read-only**.
   It must not author PRs or push commits. Give it no ruleset bypass permission.
2. In **Repository Settings → Secrets and variables → Actions**, add repository
   secret `FAST_TRACK_APP_PRIVATE_KEY` containing the dedicated App's complete PEM
   private key. No GitHub Actions environment is required.
3. Set repository variable `FAST_TRACK_APP_CLIENT_ID` to the App's client ID.
   Configure these separately in each consuming repository; values in RediSearch
   are not inherited by RediSearchEnterprise.
4. Reuse repository secrets `JIRA_USER_EMAIL` and `JIRA_API_TOKEN`. The caller
   explicitly passes these and the App key to the reusable workflow. No new Jira
   secrets or OAuth setup are needed. The credentials need read access to the
   relevant Jira issues. The client uses the fixed `https://redislabs.atlassian.net`
   endpoint.
5. Land the thin caller on the consuming repository's default branch. In
   **Actions → Request Fast-Track Approval → Run workflow**, select that default
   branch and enter your PR number.

Repository secrets are available to other eligible repository workflows; the workflow's
default-branch guard is not an environment-level restriction on secret access. This
rollout deliberately uses repository credentials.

The workflow uses pinned actions, checks out only trusted shared-release scripts, and
does not fetch or execute the PR's code. The App token is repository-scoped and limited
to pull-request writes. Approval records the policy, ticket, requester, head SHA, and
workflow-run URL.

GitHub may still require additional reviews (for example, the existing extra approval
requirement for unattributed changes). A policy approval does not promise that every
review rule is satisfied. This workflow does not alter either repository's rulesets.

## Verification and rollout

Run the credential-free tests from the repository root:

```bash
python3 -m unittest discover -s scripts/fast_track/tests -v
```

The shared repository's CI runs them on every pull request and push to `main`. Tests
cover authorization, exact label matching, ambiguous tickets, unavailable APIs, repeated
requests, paginated reviews, head changes, and compensating dismissal.

Roll out in this order:

1. Merge and validate the shared workflow, script, and tests in this repository.
2. Publish a backward-compatible release containing them and advance `v1` through
   the repository's release process.
3. Merge the product callers only after that release exists. Removing the OSS
   implementation before publication would break its manual workflow.
4. Configure the dedicated App installation, private-key secret, and client-ID
   variable in Enterprise. Its existing Jira secrets can be reused.

Before enabling routine use in each repository, verify with a controlled PR that an
eligible request creates the App review, an ineligible ticket creates no review, a
repeat is a no-op, and pushing a commit requires a new request. Confirm CI and
merge-queue requirements still block merging independently of the App approval. Unit
tests do not verify the installed App's permissions or GitHub's live ruleset behavior.

API references: - [GitHub pull-request
reviews](https://docs.github.com/en/rest/pulls/reviews) - [Jira issue
API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/) -
[Jira basic
authentication](https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/)

Existing Jira credentials were verified on 2026-10-13 by a [read-only CI
probe](https://github.com/RediSearch/RediSearch/actions/runs/38407737529):
authentication, reading MOD issue labels, and finding an `ai-fast-track` ticket all
succeeded. This verified Jira access only, not the approval App integration.

The dedicated `redisearch-ai-auto-approval-agent` was verified on 2026-10-13 in a
[controlled CI probe](https://github.com/RediSearch/RediSearch/actions/runs/38418045371)
on [draft PR #11892](https://github.com/RediSearch/RediSearch/pull/11892) targeting
`master`. With only the repository-scoped pull-request write token, it created review
`5505586125` and dismissed it; an independent API read confirmed `DISMISSED`. No ruleset
or App permissions were changed. The probe ran on `ubuntu-slim`. This verifies approval
and compensating dismissal under the current ruleset, not an end-to-end invocation of
the manual fast-track workflow or merge eligibility.
