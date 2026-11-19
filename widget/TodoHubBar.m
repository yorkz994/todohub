// Todo Hub 的菜单栏图标 + 桌面悬浮窗。界面本身是常驻服务提供的 /widget 页面，这里只是一个很薄的原生外壳：
//   菜单栏：显示未完成数；左键点开完整列表，右键是菜单。
//   悬浮窗：三种状态 —— 置顶（盖在所有窗口上）、贴在桌面（在所有窗口下面）、隐藏。可拖动、可拉伸，位置会记住。
// 用 Objective-C 写、clang 编译：只依赖 Xcode 命令行工具，不依赖 Swift 工具链。
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

static NSString *BASE = @"http://127.0.0.1:8791";

@interface DeskPanel : NSPanel
@end
@implementation DeskPanel
- (BOOL)canBecomeKeyWindow { return YES; }
@end

@interface AppDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler>
@property (strong) NSStatusItem *statusItem;
@property (strong) NSPopover *popover;
@property (strong) DeskPanel *panel;
@property (strong) WKWebView *popWeb;
@property (strong) WKWebView *deskWeb;
@property (strong) NSTimer *timer;
@property (assign) BOOL clearedOff;
@end

@implementation AppDelegate

- (NSString *)panelMode {
    NSString *m = [[NSUserDefaults standardUserDefaults] stringForKey:@"panelMode"];
    return m ? m : @"float";
}

- (void)setPanelMode:(NSString *)mode {
    [[NSUserDefaults standardUserDefaults] setObject:mode forKey:@"panelMode"];
    [self applyPanelMode];
}

- (NSURL *)widgetURL:(NSString *)mode {
    return [NSURL URLWithString:[NSString stringWithFormat:@"%@/widget?mode=%@&panel=%@", BASE, mode, [self panelMode]]];
}

- (WKWebView *)makeWeb:(NSString *)mode {
    WKWebViewConfiguration *cfg = [[WKWebViewConfiguration alloc] init];
    [cfg.userContentController addScriptMessageHandler:self name:@"hub"];
    WKWebView *w = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:cfg];
    [w setValue:@NO forKey:@"drawsBackground"];
    w.navigationDelegate = self;
    [w loadRequest:[NSURLRequest requestWithURL:[self widgetURL:mode]]];
    return w;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    NSStatusBarButton *b = self.statusItem.button;
    b.title = @"☑ …";
    b.target = self;
    b.action = @selector(statusClicked:);
    [b sendActionOn:(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)];

    self.popWeb = [self makeWeb:@"pop"];
    NSViewController *vc = [[NSViewController alloc] init];
    vc.view = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 390, 580)];
    self.popWeb.frame = vc.view.bounds;
    self.popWeb.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    [vc.view addSubview:self.popWeb];
    self.popover = [[NSPopover alloc] init];
    self.popover.contentViewController = vc;
    self.popover.contentSize = NSMakeSize(390, 580);
    self.popover.behavior = NSPopoverBehaviorTransient;

    [self buildPanel];
    [self refresh];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:30 target:self selector:@selector(refresh) userInfo:nil repeats:YES];
}

- (void)buildPanel {
    NSRect screen = [NSScreen mainScreen] ? [NSScreen mainScreen].visibleFrame : NSMakeRect(0, 0, 1440, 900);
    NSRect frame = NSMakeRect(NSMaxX(screen) - 384, NSMaxY(screen) - 660, 360, 640);
    NSString *saved = [[NSUserDefaults standardUserDefaults] stringForKey:@"panelFrame"];
    if (saved) {
        NSRect r = NSRectFromString(saved);
        BOOL onScreen = NO;
        for (NSScreen *s in [NSScreen screens]) {
            if (NSIntersectsRect(s.visibleFrame, r)) onScreen = YES;
        }
        if (r.size.width > 200 && r.size.height > 160 && onScreen) frame = r;
    }
    self.panel = [[DeskPanel alloc] initWithContentRect:frame
                                              styleMask:(NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel | NSWindowStyleMaskResizable)
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.panel.opaque = NO;
    self.panel.backgroundColor = [NSColor clearColor];
    self.panel.hasShadow = YES;
    self.panel.movableByWindowBackground = YES;
    self.panel.hidesOnDeactivate = NO;
    self.panel.minSize = NSMakeSize(260, 200);
    self.panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorIgnoresCycle | NSWindowCollectionBehaviorFullScreenAuxiliary;

    NSVisualEffectView *fx = [[NSVisualEffectView alloc] initWithFrame:NSMakeRect(0, 0, frame.size.width, frame.size.height)];
    fx.material = NSVisualEffectMaterialHUDWindow;
    fx.blendingMode = NSVisualEffectBlendingModeBehindWindow;
    fx.state = NSVisualEffectStateActive;
    fx.wantsLayer = YES;
    fx.layer.cornerRadius = 14;
    fx.layer.masksToBounds = YES;
    fx.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;

    // 顶部留一条 24pt 的把手用来拖动；网页占其余部分
    CGFloat grip = 24;
    NSTextField *label = [NSTextField labelWithString:@"Todo Hub　按住这里拖动"];
    label.font = [NSFont systemFontOfSize:10.5];
    label.textColor = [[NSColor whiteColor] colorWithAlphaComponent:0.5];
    label.frame = NSMakeRect(14, frame.size.height - grip + 3, frame.size.width - 28, 16);
    label.autoresizingMask = NSViewWidthSizable | NSViewMinYMargin;
    [fx addSubview:label];

    self.deskWeb = [self makeWeb:@"desk"];
    self.deskWeb.frame = NSMakeRect(0, 0, frame.size.width, frame.size.height - grip);
    self.deskWeb.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    [fx addSubview:self.deskWeb];
    self.panel.contentView = fx;

    NSNotificationCenter *nc = [NSNotificationCenter defaultCenter];
    [nc addObserver:self selector:@selector(saveFrame:) name:NSWindowDidMoveNotification object:self.panel];
    [nc addObserver:self selector:@selector(saveFrame:) name:NSWindowDidEndLiveResizeNotification object:self.panel];
    [self applyPanelMode];
}

- (void)saveFrame:(NSNotification *)n {
    [[NSUserDefaults standardUserDefaults] setObject:NSStringFromRect(self.panel.frame) forKey:@"panelFrame"];
}

- (void)applyPanelMode {
    if (!self.panel) return;
    NSString *m = [self panelMode];
    if ([m isEqualToString:@"hidden"]) {
        [self.panel orderOut:nil];
    } else if ([m isEqualToString:@"desk"]) {
        self.panel.level = NSNormalWindowLevel - 1;
        [self.panel orderFront:nil];
    } else {
        self.panel.level = NSFloatingWindowLevel;
        [self.panel orderFrontRegardless];
    }
    NSString *js = [NSString stringWithFormat:@"window.hubPanelMode && window.hubPanelMode('%@')", m];
    [self.deskWeb evaluateJavaScript:js completionHandler:nil];
}

#pragma mark 菜单栏

- (void)statusClicked:(id)sender {
    NSEvent *ev = [NSApp currentEvent];
    if (ev.type == NSEventTypeRightMouseUp || (ev.modifierFlags & NSEventModifierFlagControl)) {
        [self showMenu];
    } else {
        [self togglePopover];
    }
}

- (void)togglePopover {
    if (self.popover.isShown) {
        [self.popover performClose:nil];
        return;
    }
    NSStatusBarButton *b = self.statusItem.button;
    [self.popWeb evaluateJavaScript:@"window.hubRefresh && window.hubRefresh()" completionHandler:nil];
    [self.popover showRelativeToRect:b.bounds ofView:b preferredEdge:NSRectEdgeMinY];
    [NSApp activateIgnoringOtherApps:YES];
    [self.popover.contentViewController.view.window makeKeyWindow];
}

- (void)addItem:(NSString *)title action:(SEL)sel on:(BOOL)on to:(NSMenu *)menu {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:sel keyEquivalent:@""];
    item.target = self;
    item.state = on ? NSControlStateValueOn : NSControlStateValueOff;
    [menu addItem:item];
}

- (void)showMenu {
    NSMenu *m = [[NSMenu alloc] init];
    NSString *mode = [self panelMode];
    [self addItem:@"打开完整看板" action:@selector(openDashboard) on:NO to:m];
    [m addItem:[NSMenuItem separatorItem]];
    [self addItem:@"悬浮窗：置顶" action:@selector(modeFloat) on:[mode isEqualToString:@"float"] to:m];
    [self addItem:@"悬浮窗：贴在桌面" action:@selector(modeDesk) on:[mode isEqualToString:@"desk"] to:m];
    [self addItem:@"悬浮窗：隐藏" action:@selector(modeHidden) on:[mode isEqualToString:@"hidden"] to:m];
    [m addItem:[NSMenuItem separatorItem]];
    [self addItem:@"退出（不再自动启动）" action:@selector(quit) on:NO to:m];
    self.statusItem.menu = m;
    [self.statusItem.button performClick:nil];
    self.statusItem.menu = nil;
}

- (void)openDashboard { [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:BASE]]; }
- (void)modeFloat { [self setPanelMode:@"float"]; }
- (void)modeDesk { [self setPanelMode:@"desk"]; }
- (void)modeHidden { [self setPanelMode:@"hidden"]; }
- (void)quit {
    // 记下「用户主动退出」，常驻服务就不会再把它拉起来；手动再打开时会清掉这个标记
    [self post:@{@"type": @"meta.set", @"data": @{@"key": @"widget_off", @"value": @YES}} done:^{ [NSApp terminate:nil]; }];
}

#pragma mark 数据

- (void)post:(NSDictionary *)body done:(void (^)(void))done {
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[BASE stringByAppendingString:@"/api/event"]]];
    req.HTTPMethod = @"POST";
    req.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil];
    req.timeoutInterval = 4;
    [req setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    [req setValue:@"1" forHTTPHeaderField:@"X-Hub"];
    [[[NSURLSession sharedSession] dataTaskWithRequest:req completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {
        dispatch_async(dispatch_get_main_queue(), ^{ if (done) done(); });
    }] resume];
}

- (void)refresh {
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[BASE stringByAppendingString:@"/api/state"]]];
    req.timeoutInterval = 8;
    [[[NSURLSession sharedSession] dataTaskWithRequest:req completionHandler:^(NSData *data, NSURLResponse *r, NSError *e) {
        NSString *title = @"☑ –";
        BOOL wasOff = NO;
        id obj = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if ([obj isKindOfClass:[NSDictionary class]] && [obj[@"todos"] isKindOfClass:[NSArray class]]) {
            NSInteger open = 0, late = 0, due = 0;
            NSString *today = [obj[@"today"] isKindOfClass:[NSString class]] ? obj[@"today"] : @"";
            for (id t in obj[@"todos"]) {
                if (![t isKindOfClass:[NSDictionary class]] || ![t[@"status"] isEqual:@"open"]) continue;
                open++;
                if ([t[@"overdue"] isKindOfClass:[NSNumber class]] && [t[@"overdue"] boolValue]) late++;
                if ([t[@"due"] isKindOfClass:[NSString class]] && [t[@"due"] isEqualToString:today]) due++;
            }
            if (late > 0) title = [NSString stringWithFormat:@"☑ %ld · 逾期 %ld", (long)open, (long)late];
            else if (due > 0) title = [NSString stringWithFormat:@"☑ %ld · 今天 %ld", (long)open, (long)due];
            else title = [NSString stringWithFormat:@"☑ %ld", (long)open];
            id meta = obj[@"meta"];
            if ([meta isKindOfClass:[NSDictionary class]] && [meta[@"widget_off"] isKindOfClass:[NSNumber class]] && [meta[@"widget_off"] boolValue]) wasOff = YES;
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            self.statusItem.button.title = title;
            if (wasOff && !self.clearedOff) {
                self.clearedOff = YES;
                [self post:@{@"type": @"meta.set", @"data": @{@"key": @"widget_off", @"value": @NO}} done:nil];
            }
        });
    }] resume];
}

#pragma mark 网页 ↔ 原生

- (void)userContentController:(WKUserContentController *)userContentController didReceiveScriptMessage:(WKScriptMessage *)message {
    id body = message.body;
    if (![body isKindOfClass:[NSDictionary class]]) return;
    NSString *cmd = body[@"cmd"];
    if ([cmd isEqual:@"changed"]) {
        [self refresh];
        WKWebView *other = (message.webView == self.popWeb) ? self.deskWeb : self.popWeb;
        [other evaluateJavaScript:@"window.hubRefresh && window.hubRefresh()" completionHandler:nil];
    } else if ([cmd isEqual:@"open"]) {
        [self openDashboard];
    } else if ([cmd isEqual:@"mode"] && [body[@"mode"] isKindOfClass:[NSString class]]) {
        [self setPanelMode:body[@"mode"]];
    }
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))handler {
    if (action.navigationType == WKNavigationTypeLinkActivated && action.request.URL) {
        [[NSWorkspace sharedWorkspace] openURL:action.request.URL];
        handler(WKNavigationActionPolicyCancel);
        return;
    }
    handler(WKNavigationActionPolicyAllow);
}

- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    // 常驻服务还没起来时，每 5 秒重试
    NSString *mode = (webView == self.popWeb) ? @"pop" : @"desk";
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(5 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [webView loadRequest:[NSURLRequest requestWithURL:[self widgetURL:mode]]];
    });
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc > 1) {
            NSString *a = [NSString stringWithUTF8String:argv[1]];
            if ([a hasPrefix:@"http"]) BASE = a;
        }
        NSApplication *app = [NSApplication sharedApplication];
        AppDelegate *delegate = [[AppDelegate alloc] init];
        app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
