import SwiftUI
import MapKit

// MARK: - StreetPolyline (carries Street PK alongside the geometry)

final class StreetPolyline: MKPolyline {
    var streetId: Int = 0
    var streetName: String = ""
}

// MARK: - StreetMapView (UIViewRepresentable)

struct StreetMapView: UIViewRepresentable {
    let streets: [Street]
    @Binding var selectedIds: Set<Int>
    let bbox: [[Double]]?

    // MARK: - Make

    func makeUIView(context: Context) -> MKMapView {
        let mapView = MKMapView()
        mapView.delegate = context.coordinator
        mapView.showsUserLocation = true
        mapView.mapType = .standard

        let tap = UITapGestureRecognizer(target: context.coordinator,
                                        action: #selector(Coordinator.handleTap(_:)))
        tap.delegate = context.coordinator
        mapView.addGestureRecognizer(tap)
        return mapView
    }

    // MARK: - Update

    func updateUIView(_ mapView: MKMapView, context: Context) {
        let coordinator = context.coordinator
        coordinator.parent = self

        // Load street overlays once when streets first arrive
        if coordinator.loadedStreetCount != streets.count {
            mapView.removeOverlays(mapView.overlays)
            coordinator.polylineById = [:]

            var overlays: [MKOverlay] = []
            for street in streets {
                var coords = street.coordinates
                let poly = StreetPolyline(coordinates: &coords, count: coords.count)
                poly.streetId = street.id
                poly.streetName = street.name
                coordinator.polylineById[street.id] = poly
                overlays.append(poly)
            }
            mapView.addOverlays(overlays, level: .aboveRoads)
            coordinator.loadedStreetCount = streets.count
            coordinator.renderedSelection = []

            if let bbox = bbox, bbox.count >= 2 {
                setRegion(on: mapView, bbox: bbox)
            }
        }

        // Refresh renderer colors when selection changes
        guard coordinator.renderedSelection != selectedIds else { return }
        let changed = coordinator.renderedSelection.symmetricDifference(selectedIds)
        coordinator.renderedSelection = selectedIds

        for streetId in changed {
            guard let poly = coordinator.polylineById[streetId],
                  let renderer = mapView.renderer(for: poly) as? MKPolylineRenderer else { continue }
            let isSelected = selectedIds.contains(streetId)
            renderer.strokeColor = isSelected ? .leafletterGreen : .streetGray
            renderer.lineWidth = isSelected ? 5 : 2
            renderer.setNeedsDisplay()
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    // MARK: - Region helper

    private func setRegion(on mapView: MKMapView, bbox: [[Double]]) {
        // bbox format: [[sw_lat, sw_lon], [ne_lat, ne_lon]]
        let sw = CLLocationCoordinate2D(latitude: bbox[0][0], longitude: bbox[0][1])
        let ne = CLLocationCoordinate2D(latitude: bbox[1][0], longitude: bbox[1][1])
        let center = CLLocationCoordinate2D(
            latitude: (sw.latitude + ne.latitude) / 2,
            longitude: (sw.longitude + ne.longitude) / 2
        )
        let span = MKCoordinateSpan(
            latitudeDelta: abs(ne.latitude - sw.latitude) * 1.3,
            longitudeDelta: abs(ne.longitude - sw.longitude) * 1.3
        )
        mapView.setRegion(MKCoordinateRegion(center: center, span: span), animated: false)
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, MKMapViewDelegate, UIGestureRecognizerDelegate {
        var parent: StreetMapView
        var loadedStreetCount = 0
        var renderedSelection: Set<Int> = []
        var polylineById: [Int: StreetPolyline] = [:]

        init(_ parent: StreetMapView) { self.parent = parent }

        // MARK: Overlay renderer

        func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
            guard let poly = overlay as? StreetPolyline else {
                return MKOverlayRenderer(overlay: overlay)
            }
            let renderer = MKPolylineRenderer(polyline: poly)
            let isSelected = parent.selectedIds.contains(poly.streetId)
            renderer.strokeColor = isSelected ? .leafletterGreen : .streetGray
            renderer.lineWidth = isSelected ? 5 : 2
            return renderer
        }

        // MARK: Tap handling

        @objc func handleTap(_ gesture: UITapGestureRecognizer) {
            guard gesture.state == .ended,
                  let mapView = gesture.view as? MKMapView else { return }

            let tapPt = gesture.location(in: mapView)
            let tapCoord = mapView.convert(tapPt, toCoordinateFrom: mapView)
            let tapMapPt = MKMapPoint(tapCoord)

            // Threshold: 25 meters expressed in MKMapPoints
            let threshold = 25.0 * MKMapPointsPerMeterAtLatitude(tapCoord.latitude)

            var best: (distance: Double, id: Int)?

            for overlay in mapView.overlays {
                guard let poly = overlay as? StreetPolyline else { continue }
                let points = poly.points()
                for i in 0..<max(0, poly.pointCount - 1) {
                    let d = distanceFromPoint(tapMapPt, segA: points[i], segB: points[i + 1])
                    if d < threshold, best == nil || d < best!.distance {
                        best = (d, poly.streetId)
                    }
                }
            }

            guard let hit = best else { return }
            if parent.selectedIds.contains(hit.id) {
                parent.selectedIds.remove(hit.id)
            } else {
                parent.selectedIds.insert(hit.id)
            }
        }

        // Allow the tap gesture to coexist with MapKit's built-in gestures
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            true
        }

        // MARK: Geometry

        private func distanceFromPoint(_ p: MKMapPoint, segA a: MKMapPoint, segB b: MKMapPoint) -> Double {
            let dx = b.x - a.x, dy = b.y - a.y
            let lenSq = dx * dx + dy * dy
            if lenSq == 0 { return hypot(p.x - a.x, p.y - a.y) }
            let t = max(0, min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq))
            return hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
        }
    }
}

// MARK: - Color constants

extension UIColor {
    static let leafletterGreen = UIColor(red: 0.10, green: 0.42, blue: 0.24, alpha: 1.0)
    static let streetGray = UIColor(white: 0.55, alpha: 1.0)
}
