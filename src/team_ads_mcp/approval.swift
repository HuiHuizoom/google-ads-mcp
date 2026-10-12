import AppKit
import LocalAuthentication

// Independent human presence boundary. No CLI flag or model-provided boolean approves.
guard CommandLine.arguments.count == 4 else { exit(2) }
let reviewPath = CommandLine.arguments[1]
let hash = CommandLine.arguments[2]
let customer = CommandLine.arguments[3]
guard let content = try? String(contentsOfFile: reviewPath, encoding: .utf8) else { exit(2) }
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let alert = NSAlert()
alert.alertStyle = .warning
alert.messageText = "Google Ads：审核并批准具体变更"
alert.informativeText = "账户 \(customer)\n计划 SHA-256：\(hash)\n请核对下方计划。批准后 MCP 才可提交。预算调整/启用可能产生实际花费。"
alert.addButton(withTitle: "取消")
alert.addButton(withTitle: "已审核，继续身份验证")
let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 800, height: 500))
scroll.hasVerticalScroller = true
scroll.hasHorizontalScroller = true
let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 780, height: 500))
text.isEditable = false
text.isSelectable = true
text.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
text.string = content
text.isVerticallyResizable = true
text.autoresizingMask = [.width]
text.textContainer?.widthTracksTextView = true
scroll.documentView = text
alert.accessoryView = scroll
application.activate(ignoringOtherApps: true)
guard alert.runModal() == .alertSecondButtonReturn else { exit(3) }
let context = LAContext()
var error: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { exit(4) }
var finished = false
var approved = false
context.evaluatePolicy(.deviceOwnerAuthentication,
    localizedReason: "批准 Google Ads 账户 \(customer) 变更计划 \(hash.prefix(12))") { success, _ in
    DispatchQueue.main.async {
        approved = success
        finished = true
    }
}
while !finished { RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.1)) }
guard approved else { exit(5) }
print("APPROVED \(hash)")
