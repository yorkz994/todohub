// 由 reminders.py 通过 `osascript -l JavaScript reminders.jxa.js <请求文件>` 调用。
// 请求：{op:"read"|"apply", list:"Todo Hub", create:[{name,body,due}], update:[{id,name,body,due,completed}]}
// 输出：一行 JSON。只碰指定的那一个列表，别的列表不读不写。
ObjC.import('Foundation');

function readText(path) {
  return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError($(path), $.NSUTF8StringEncoding, null));
}

function toDate(s) {
  // "YYYY-MM-DD" 按本地时间上午 9 点；其余交给 Date 解析
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], 9, 0, 0) : new Date(s);
}

function run(argv) {
  var req = JSON.parse(readText(argv[0]));
  var app = Application('Reminders');
  var out = {ok: true, created_list: false, items: [], errors: []};

  if (req.op === 'dump') {
    // 只读：把所有列表导出来。已完成的只保留 req.since 之后完成的，避免把多年的历史都带上。
    var since = req.since ? new Date(req.since) : null, all = app.lists();
    out.lists = [];
    for (var li = 0; li < all.length; li++) {
      var entry = {name: all[li].name(), items: [], skipped_done: 0};
      try {
        var r = all[li].reminders;
        if (r.length > 0) {
          var ri = r.id(), rn = r.name(), rb = r.body(), rd = r.completed(), ru = r.dueDate(), rc = r.completionDate(), rm = r.creationDate(), rp = r.priority();
          for (var k = 0; k < ri.length; k++) {
            if (rd[k] && since && rc[k] && rc[k] < since) { entry.skipped_done++; continue; }
            entry.items.push({id: ri[k], name: rn[k], body: rb[k] || '', completed: !!rd[k], priority: rp[k] || 0,
              due: ru[k] ? ru[k].toISOString() : null, done_at: rc[k] ? rc[k].toISOString() : null,
              created: rm[k] ? rm[k].toISOString() : null});
          }
        }
      } catch (e) { entry.error = e.message; }
      out.lists.push(entry);
    }
    return JSON.stringify(out);
  }

  if (app.lists.name().indexOf(req.list) < 0) {
    try {
      app.make({new: 'list', withProperties: {name: req.list}});
    } catch (e) {
      app.lists.push(app.List({name: req.list}));
    }
    out.created_list = true;
  }
  var list = app.lists.byName(req.list);

  if (req.op === 'apply') {
    (req.update || []).forEach(function (u) {
      try {
        var r = (u.list ? app.lists.byName(u.list) : list).reminders.byId(u.id);
        if (u.name !== undefined) r.name = u.name;
        if (u.body !== undefined) r.body = u.body;
        if (u.due) r.dueDate = toDate(u.due);
        if (u.completed !== undefined) r.completed = u.completed;
      } catch (e) {
        out.errors.push('update ' + u.id + ': ' + e.message);
      }
    });
    (req.create || []).forEach(function (c) {
      try {
        var props = {name: c.name, body: c.body || ''};
        if (c.due) props.dueDate = toDate(c.due);
        list.reminders.push(app.Reminder(props));
      } catch (e) {
        out.errors.push('create ' + c.name + ': ' + e.message);
      }
    });
  }

  // 镜像列表之外，再读「接管」的列表（你原来就在用的那些）。只读它们的条目，不建不删。
  var existing = app.lists.name();
  [req.list].concat(req.also || []).forEach(function (name) {
    if (existing.indexOf(name) < 0 && name !== req.list) return;
    var rs = app.lists.byName(name).reminders;
    if (rs.length === 0) return;
    var ids = rs.id(), names = rs.name(), bodies = rs.body(), done = rs.completed(), dues = rs.dueDate();
    for (var i = 0; i < ids.length; i++) {
      out.items.push({
        id: ids[i], list: name, name: names[i], body: bodies[i] || '', completed: !!done[i],
        due: dues[i] ? dues[i].toISOString() : null
      });
    }
  });
  return JSON.stringify(out);
}
