// Shared data model — the iOS mirror of the desktop engine's Timeline.
import Foundation

enum ClipRole { case primary, overlay, music }

struct SourceClip: Identifiable, Equatable {
    let id = UUID()
    let url: URL
    var role: ClipRole
    var duration: Double = 0
    var isPortrait: Bool = false
    var creationDate: Date?

    static func == (lhs: SourceClip, rhs: SourceClip) -> Bool { lhs.id == rhs.id }
}

struct Interval {
    let start: Double
    let end: Double
}

struct PlacedOverlay: Identifiable {
    let id = UUID()
    let clip: SourceClip
    let timelineStart: Double
    let duration: Double
    let confidence: Double
}

enum OverlayStyle: String, CaseIterable, Identifiable {
    case center = "Centered"
    case fill = "Fill the frame"
    case pipLeft = "Small · left"
    case pipRight = "Small · right"
    var id: String { rawValue }
}

enum TitleStyleKey: String, CaseIterable, Identifiable {
    case classic = "Classic"
    case lowerLeft = "Lower left"
    case statement = "Statement"
    case elegant = "Elegant"
    var id: String { rawValue }
}

struct TitleSpec {
    var title: String = ""
    var descriptionLine: String = ""
    var style: TitleStyleKey = .classic
    var hold: Double = 3.0
    var fade: Double = 1.0
    var isEnabled: Bool { !title.trimmingCharacters(in: .whitespaces).isEmpty }
}

struct SyncOptions {
    var overlayStyle: OverlayStyle = .center
    var duckPrimary: Bool = true       // mute the main cam under overlays
    var musicEnabled: Bool = false
    var musicDb: Double = -22
    var musicDuck: Bool = false
    var title = TitleSpec()
}

/// The fully-resolved edit, ready for AVFoundation composition.
struct TimelinePlan {
    var primaries: [SourceClip] = []          // chronological order
    var primaryStarts: [Double] = []          // timeline start of each primary
    var overlays: [PlacedOverlay] = []
    var duckRegions: [Interval] = []          // primary audio down here
    var music: SourceClip?
    var musicDb: Double = -22
    var musicDuckRegions: [Interval] = []
    var title: TitleSpec?
    var totalDuration: Double = 0
    var unplaced: [SourceClip] = []           // overlays we could not match
}
