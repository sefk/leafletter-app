import SwiftUI
import MapKit

// MARK: - StreetPolyline (carries Street PK alongside the geometry)

final class StreetPolyline: MKPolyline {
    var streetId: Int = 0
    var streetName: String = ""
}

// MARK: - StreetMapView (UIViewRepresentable)

// Plain MKPolyline subclass to tag coverage overlays
final class CoveragePolyline: MKPolyline {
    var tripId: String = ""
}

struct StreetMapView: UIViewRepresentable {
    let streets: [Street]
    let coveredStreets: [CoveredStreet]
    @Binding var selectedIds: Set<Int>
    let bbox: [[Double]]?
    @Binding var lassoMode: Bool

    // MARK: - Make

    func makeUIView(context: Context) -> MKMapView {
        let mapView = MKMapView()
        mapView.delegate = context.coordinator
        mapView.showsUserLocation = true
        mapView.mapType = .standard
        mapView.isScrollEnabled = false  // lasso is default

        // Tap for individual street toggle
        let tap = UITapGestureRecognizer(target: context.coordinator,
                                        action: #selector(Coordinator.handleTap(_:)))
        tap.delegate = context.coordinator
        mapView.addGestureRecognizer(tap)

        // One-finger pan for lasso drawing
        let pan = UIPanGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.handleLasso(_:)))
        pan.minimumNumberOfTouches = 1
        pan.maximumNumberOfTouches = 1
        pan.delegate = context.coordinator
        mapView.addGestureRecognizer(pan)
        context.coordinator.lassoPan = pan

        return mapView
    }

    // MARK: - Update

    func updateUIView(_ mapView: MKMapView, context: Context) {
        let coordinator = context.coordinator
        coordinator.parent = self

        // Sync lasso / pan mode
        mapView.isScrollEnabled = !lassoMode
        coordinator.lassoPan?.isEnabled = lassoMode

        // Load coverage overlays when coverage changes (rendered below street network)
        if coordinator.loadedCoverageCount != coveredStreets.count {
            // Remove existing coverage polylines
            let oldCoverage = mapView.overlays.filter { $0 is CoveragePolyline }
            mapView.removeOverlays(oldCoverage)

            var coverageOverlays: [MKOverlay] = []
            for covered in coveredStreets {
                var coords = covered.coordinates
                let poly = CoveragePolyline(coordinates: &coords, count: coords.count)
                poly.tripId = covered.tripId
                coverageOverlays.append(poly)
            }
            // Insert at index 0 so coverage renders below street overlays
            mapView.insertOverlays(coverageOverlays, at: 0, level: .aboveRoads)
            coordinator.loadedCoverageCount = coveredStreets.count
        }

        // Load street overlays once when streets first arrive
        if coordinator.loadedStreetCount != streets.count {
            let oldStreets = mapView.overlays.filter { $0 is StreetPolyline }
            mapView.removeOverlays(oldStreets)
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
        var loadedCoverageCount = 0
        var renderedSelection: Set<Int> = []
        var polylineById: [Int: StreetPolyline] = [:]

        // Lasso state
        weak var lassoPan: UIPanGestureRecognizer?
        private var lassoPoints: [CGPoint] = []
        private var lassoLayer: CAShapeLayer?

        init(_ parent: StreetMapView) { self.parent = parent }

        // MARK: Overlay renderer

        func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
            if let coverage = overlay as? CoveragePolyline {
                let renderer = MKPolylineRenderer(polyline: coverage)
                renderer.strokeColor = UIColor(red: 0.95, green: 0.55, blue: 0.1, alpha: 0.7)
                renderer.lineWidth = 4
                return renderer
            }
            guard let poly = overlay as? StreetPolyline else {
                return MKOverlayRenderer(overlay: overlay)
            }
            let renderer = MKPolylineRenderer(polyline: poly)
            let isSelected = parent.selectedIds.contains(poly.streetId)
            renderer.strokeColor = isSelected ? .leafletterGreen : .streetGray
            renderer.lineWidth = isSelected ? 5 : 2
            return renderer
        }

        // MARK: Tap — toggle individual street

        @objc func handleTap(_ gesture: UITapGestureRecognizer) {
            guard gesture.state == .ended,
                  let mapView = gesture.view as? MKMapView else { return }

            let tapPt = gesture.location(in: mapView)
            let tapCoord = mapView.convert(tapPt, toCoordinateFrom: mapView)
            let tapMapPt = MKMapPoint(tapCoord)
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

        // MARK: Lasso — draw & select

        @objc func handleLasso(_ gesture: UIPanGestureRecognizer) {
            guard let mapView = gesture.view as? MKMapView else { return }
            let pt = gesture.location(in: mapView)

            switch gesture.state {
            case .began:
                lassoPoints = [pt]
                let layer = CAShapeLayer()
                layer.strokeColor = UIColor.systemBlue.cgColor
                layer.fillColor = UIColor.systemBlue.withAlphaComponent(0.12).cgColor
                layer.lineWidth = 2
                layer.lineDashPattern = [6, 3]
                mapView.layer.addSublayer(layer)
                lassoLayer = layer

            case .changed:
                lassoPoints.append(pt)
                let path = UIBezierPath()
                path.move(to: lassoPoints[0])
                for p in lassoPoints.dropFirst() { path.addLine(to: p) }
                path.close()
                lassoLayer?.path = path.cgPath

            case .ended:
                lassoLayer?.removeFromSuperlayer()
                lassoLayer = nil
                if lassoPoints.count > 3 {
                    selectStreetsInLasso(mapView: mapView)
                }
                lassoPoints = []

            case .cancelled, .failed:
                lassoLayer?.removeFromSuperlayer()
                lassoLayer = nil
                lassoPoints = []

            default:
                break
            }
        }

        private func selectStreetsInLasso(mapView: MKMapView) {
            let lassoMapPts = lassoPoints.map {
                MKMapPoint(mapView.convert($0, toCoordinateFrom: mapView))
            }
            for overlay in mapView.overlays {
                guard let poly = overlay as? StreetPolyline else { continue }
                if polylineIntersectsLasso(poly: poly, lasso: lassoMapPts) {
                    parent.selectedIds.insert(poly.streetId)
                }
            }
        }

        private func polylineIntersectsLasso(poly: StreetPolyline, lasso: [MKMapPoint]) -> Bool {
            let pts = poly.points()
            for i in 0..<poly.pointCount {
                if pointInPolygon(pts[i], polygon: lasso) { return true }
            }
            for i in 0..<max(0, poly.pointCount - 1) {
                for j in 0..<lasso.count {
                    if segmentsIntersect(lasso[j], lasso[(j + 1) % lasso.count],
                                         pts[i], pts[i + 1]) { return true }
                }
            }
            return false
        }

        // Ray-casting point-in-polygon
        private func pointInPolygon(_ p: MKMapPoint, polygon: [MKMapPoint]) -> Bool {
            var inside = false
            var j = polygon.count - 1
            for i in 0..<polygon.count {
                let xi = polygon[i].x, yi = polygon[i].y
                let xj = polygon[j].x, yj = polygon[j].y
                if ((yi > p.y) != (yj > p.y)) &&
                   (p.x < (xj - xi) * (p.y - yi) / (yj - yi) + xi) {
                    inside.toggle()
                }
                j = i
            }
            return inside
        }

        private func segmentsIntersect(_ a1: MKMapPoint, _ a2: MKMapPoint,
                                       _ b1: MKMapPoint, _ b2: MKMapPoint) -> Bool {
            func cross(_ o: MKMapPoint, _ a: MKMapPoint, _ b: MKMapPoint) -> Double {
                (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
            }
            let d1 = cross(b1, b2, a1), d2 = cross(b1, b2, a2)
            let d3 = cross(a1, a2, b1), d4 = cross(a1, a2, b2)
            return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
                   ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
        }

        // MARK: Gesture coexistence

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            // Tap can coexist with everything; lasso pan should not coexist with MapKit pan
            gestureRecognizer is UITapGestureRecognizer
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
