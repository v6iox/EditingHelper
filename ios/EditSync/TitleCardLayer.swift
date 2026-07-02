// The title card as a CALayer (for export burn-in) and layout math
// shared with the SwiftUI preview overlay — mirrors the desktop styles.
import UIKit

enum TitleCardLayer {

    struct Layout {
        let titleFont: UIFont
        let descFont: UIFont
        let titleText: String
        let descText: String
        let centerXFraction: CGFloat  // 0-1 of width
        let centerYFraction: CGFloat  // 0-1 of height (0 = top)
        let leftAligned: Bool
        let descColor: UIColor
    }

    static func layout(spec: TitleSpec, height: CGFloat) -> Layout {
        let factor = height / 1080
        switch spec.style {
        case .classic:
            return Layout(
                titleFont: .systemFont(ofSize: 92 * factor, weight: .bold),
                descFont: .systemFont(ofSize: 48 * factor),
                titleText: spec.title,
                descText: spec.descriptionLine,
                centerXFraction: 0.5, centerYFraction: 0.48,
                leftAligned: false,
                descColor: UIColor(white: 0.25, alpha: 1))
        case .lowerLeft:
            return Layout(
                titleFont: .systemFont(ofSize: 84 * factor, weight: .bold),
                descFont: .systemFont(ofSize: 44 * factor),
                titleText: spec.title,
                descText: spec.descriptionLine,
                centerXFraction: 0.26, centerYFraction: 0.74,
                leftAligned: true,
                descColor: UIColor(white: 0.25, alpha: 1))
        case .statement:
            return Layout(
                titleFont: .systemFont(ofSize: 118 * factor, weight: .black),
                descFont: .systemFont(ofSize: 40 * factor),
                titleText: spec.title.uppercased(),
                descText: spec.descriptionLine.uppercased(),
                centerXFraction: 0.5, centerYFraction: 0.46,
                leftAligned: false,
                descColor: UIColor(white: 0.25, alpha: 1))
        case .elegant:
            return Layout(
                titleFont: UIFont(name: "Georgia", size: 90 * factor)
                    ?? .systemFont(ofSize: 90 * factor),
                descFont: .systemFont(ofSize: 42 * factor),
                titleText: spec.title,
                descText: spec.descriptionLine.uppercased(),
                centerXFraction: 0.5, centerYFraction: 0.48,
                leftAligned: false,
                descColor: UIColor(white: 0.4, alpha: 1))
        }
    }

    static func make(spec: TitleSpec, size: CGSize) -> CALayer {
        let card = CALayer()
        card.frame = CGRect(origin: .zero, size: size)
        card.backgroundColor = UIColor.white.cgColor

        let l = layout(spec: spec, height: size.height)
        let gap = 60 * size.height / 1080
        let titleH = ceil(l.titleFont.lineHeight)
        let descH = l.descText.isEmpty ? 0 : ceil(l.descFont.lineHeight)
        let blockH = titleH + (descH > 0 ? gap + descH : 0)
        let centerY = size.height * l.centerYFraction
        var y = centerY - blockH / 2

        func textLayer(_ text: String, font: UIFont, color: UIColor,
                       y: CGFloat, height: CGFloat) -> CATextLayer {
            let layer = CATextLayer()
            layer.string = text
            layer.font = font
            layer.fontSize = font.pointSize
            layer.foregroundColor = color.cgColor
            layer.contentsScale = UIScreen.main.scale
            layer.alignmentMode = l.leftAligned ? .left : .center
            let x = l.leftAligned ? size.width * (l.centerXFraction - 0.18) : 0
            let width = l.leftAligned ? size.width - x : size.width
            layer.frame = CGRect(x: x, y: y, width: width, height: height * 1.3)
            return layer
        }

        card.addSublayer(
            textLayer(l.titleText, font: l.titleFont, color: .black,
                      y: y, height: titleH))
        if descH > 0 {
            y += titleH + gap
            card.addSublayer(
                textLayer(l.descText, font: l.descFont, color: l.descColor,
                          y: y, height: descH))
        }
        return card
    }
}
