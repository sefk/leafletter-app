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
