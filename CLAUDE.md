## Deploy / Branch Strategy

- `main` branch → Railway `staging` environment (auto-deploys on push)
- `prod` branch → Railway `production` environment (manual: fast-forward `prod` to `main` and push)
- Never push directly to `prod` without reviewing on staging first
- To release: `git checkout prod && git merge --ff-only main && git push origin prod && git checkout main`

## iOS Development

Before committing any iOS changes, the Xcode build must pass without errors:

    xcodebuild -project ios/Leafletter/Leafletter.xcodeproj
      -scheme Leafletter
      -destination 'platform=iOS Simulator,name=iPhone 16'
      build | xcpretty

Claude should not ask for permission to run xcodebuild.

Do not commit if the build fails.

No special permissions are needed beyond what the main agent already
has — just ask it to make the edit.
