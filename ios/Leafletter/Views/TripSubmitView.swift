import SwiftUI

struct TripSubmitView: View {
    let campaign: Campaign
    let selectedIds: [Int]
    let onSuccess: () -> Void

    @State private var workerName = ""
    @State private var notes = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?
    @State private var submittedTripId: String?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            if let tripId = submittedTripId {
                successView(tripId: tripId)
            } else {
                formView
            }
        }
    }

    // MARK: - Form

    private var formView: some View {
        Form {
            Section {
                HStack {
                    Text("Blocks selected")
                    Spacer()
                    Text("\(selectedIds.count)")
                        .foregroundStyle(.secondary)
                }
            }

            Section("Your info (optional)") {
                TextField("Name", text: $workerName)
                    .textContentType(.name)
                    .autocorrectionDisabled()
            }

            Section("Notes (optional)") {
                TextEditor(text: $notes)
                    .frame(minHeight: 80)
            }

            if let error = errorMessage {
                Section {
                    Label(error, systemImage: "exclamationmark.circle")
                        .foregroundStyle(.red)
                }
            }
        }
        .navigationTitle("Log a Trip")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancel") { dismiss() }
                    .disabled(isSubmitting)
            }
            ToolbarItem(placement: .confirmationAction) {
                if isSubmitting {
                    ProgressView()
                } else {
                    Button("Submit") { Task { await submit() } }
                        .bold()
                }
            }
        }
    }

    // MARK: - Success

    private func successView(tripId: String) -> some View {
        VStack(spacing: 24) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 72))
                .foregroundStyle(.green)

            Text("Trip Logged!")
                .font(.title.bold())

            Text("\(selectedIds.count) block\(selectedIds.count == 1 ? "" : "s") recorded for \(campaign.name).")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)

            Button("Done") {
                onSuccess()
            }
            .buttonStyle(.borderedProminent)
            .tint(Color(red: 0.1, green: 0.42, blue: 0.24))
        }
        .padding()
        .navigationTitle("Trip Logged")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - Submit

    private func submit() async {
        guard !selectedIds.isEmpty else { return }
        isSubmitting = true
        errorMessage = nil
        do {
            let response = try await APIClient.shared.submitTrip(
                slug: campaign.slug,
                segmentIds: selectedIds,
                workerName: workerName.trimmingCharacters(in: .whitespaces),
                notes: notes.trimmingCharacters(in: .whitespaces)
            )
            submittedTripId = response.tripId
        } catch {
            errorMessage = error.localizedDescription
        }
        isSubmitting = false
    }
}
