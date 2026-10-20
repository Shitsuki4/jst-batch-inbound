using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
namespace Receiving.Native;

// 录像查询（视频举证库）. Rebuilt from scratch in the next release:
// - one grid selection model: the checkbox column is bound to DataGridRow.IsSelected, so Ctrl/Shift
//   clicks, row checkboxes and the header "select all" checkbox are the same selection;
// - one delete path (VideoLibrary.DeleteSelected) for "删除所选" and for old-recording cleanup;
// - loading runs off the UI thread and the grid keeps row/column virtualization on.

public sealed record ScanCue(double Offset, DateTimeOffset At, string Sku, string Name, string Barcode, string Quantity) {
 public string OffsetText => VideoLibraryView.Clock(Offset);
 public string Product => string.IsNullOrWhiteSpace(Name) ? Sku : Sku + "  " + Name;
}

// Everything the view needs from the app. MainWindow implements it for real use;
// the standalone acceptance test implements it without backend, camera or business window.
public interface IVideoLibraryHost {
 VideoLibrary Store { get; }
 string? ActiveVideoId { get; }
 bool Recording { get; }
 bool NotesEnabled { get; }
 bool ProtectionEnabled { get; }
 int CleanupDays { get; }
 string Ffmpeg { get; }
 string PlayerFolder { get; }
 Task<CoreWebView2Environment> WebEnvironment();
 Task<int> RefreshAssociations(IReadOnlyList<LibraryItem> items);
 Task RefreshContext(LibraryItem item);
 Task<HashSet<string>> FindByProduct(string query);
 Task<IReadOnlyList<ScanCue>> LoadCues(LibraryItem item);
 Task<T> Exclusive<T>(Func<string?, Task<T>> action);
 Task AfterChange(LibraryItem item, string? movedTo = null);
 Task AfterDelete();
}

public enum LibraryPreset { Today, Last7, Last30, All, Custom }

public sealed class VideoLibraryRow {
 static readonly Brush Blue = Frozen(0x1D, 0x6F, 0xD8), Green = Frozen(0x16, 0x8A, 0x4A), Red = Frozen(0xC6, 0x28, 0x28), Amber = Frozen(0xB4, 0x6A, 0x00), Plain = Frozen(0x33, 0x41, 0x55);
 static Brush Frozen(byte r, byte g, byte b) { var x = new SolidColorBrush(Color.FromRgb(r, g, b)); x.Freeze(); return x; }
 public VideoLibraryRow(LibraryItem item, string? activeId) { Item = item; Live = item.Id == activeId; }
 public LibraryItem Item { get; }
 public bool Live { get; }
 public string Id => Item.Id;
 public long StartTicks => Item.At.UtcTicks;
 public string StartText => Item.At.LocalDateTime.ToString("yyyy-MM-dd HH:mm:ss");
 public double Duration => Item.DurationSeconds;
 public string DurationText => Live ? "录制中" : Duration > 0 ? VideoLibraryView.Clock(Duration) : "—";
 public string Tracking => Item.Tracking;
 public string Purchase => Item.Purchase;
 public string Outer => Item.Linked.Purchases.Count == 0 ? "" : Item.Outer;
 public string Status => Live ? "在录" : Item.State == "saved" ? "已完成" : "异常";
 public Brush StatusBrush => Live ? Blue : Item.State == "saved" ? Green : Red;
 public long Bytes => Item.Bytes;
 public string SizeText => Item.Bytes > 0 ? VideoLibraryView.Size(Item.Bytes) : Live ? "写入中" : "文件缺失";
 public string Protection => Item.Protected ? "售后保护" : "—";
 public Brush ProtectionBrush => Item.Protected ? Amber : Plain;
 public string Archive => Item.ArchiveStatus;
 public string Note => Item.Note;
 public string Tip => Item.File + (Item.State != "saved" && !Live && Item.Segment.Reason != "" ? "\n原因：" + Item.Segment.Reason : "");
 public bool InStatus(string filter) => filter switch {
  "已完成" => !Live && Item.State == "saved",
  "在录" => Live,
  "异常" => !Live && Item.State != "saved",
  "售后保护" => Item.Protected,
  "已归档" => Item.Archived,
  _ => true
 };
}

// DataGrid exposes the batch selection API only to subclasses; used for select-all / cleanup selection.
public sealed class LibraryGrid : DataGrid {
 public void SelectRows(IEnumerable<object> items) {
  BeginUpdateSelectedItems();
  try { SelectedItems.Clear(); foreach (var i in items) SelectedItems.Add(i); }
  finally { EndUpdateSelectedItems(); }
 }
}

public sealed class VideoLibraryView : DockPanel {
 public static string Clock(double seconds) { if (!double.IsFinite(seconds) || seconds < 0) seconds = 0; var t = TimeSpan.FromSeconds(Math.Floor(seconds)); return t.TotalHours >= 1 ? t.ToString(@"h\:mm\:ss") : t.ToString(@"mm\:ss"); }
 public static string Size(long bytes) => bytes >= 1L << 30 ? $"{bytes / 1073741824.0:0.00} GB" : $"{bytes / 1048576.0:0.0} MB";
 public const string Placeholder = "扫码或输入：物流 / 采购 / 外部单号 / 商品编码 / 备注";

 readonly IVideoLibraryHost host;
 public TextBox Query { get; } = new() { MinWidth = 280, FontSize = 16, Padding = new Thickness(10, 7, 10, 7) };
 public LibraryGrid Grid { get; } = new();
 public LibraryPlayer Player { get; }
 public CheckBox HeaderCheck { get; } = new() { HorizontalAlignment = HorizontalAlignment.Center, ToolTip = "全选 / 取消全选当前列表" };
 public ComboBox StatusBox { get; } = new() { Width = 110 };
 public ComboBox CleanupBox { get; } = new() { Width = 110 };
 public DatePicker From { get; } = new() { Width = 128 };
 public DatePicker To { get; } = new() { Width = 128 };
 public TextBlock Summary { get; } = new() { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 0, 0), FontWeight = FontWeights.SemiBold };
 public TextBlock Notice { get; } = new() { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(8, 6, 8, 6) };
 readonly Dictionary<LibraryPreset, RadioButton> presets = new();
 readonly TextBlock placeholder = new() { Text = Placeholder, Foreground = Brushes.Gray, IsHitTestVisible = false, Margin = new Thickness(13, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center, FontSize = 15 };
 readonly DispatcherTimer debounce = new() { Interval = TimeSpan.FromMilliseconds(300) };
 readonly ColumnDefinition gridColumn = new() { Width = new GridLength(1, GridUnitType.Star), MinWidth = 360 }, splitColumn = new() { Width = new GridLength(6) }, playerColumn = new() { Width = new GridLength(430), MinWidth = 320 };
 readonly UIElement topArea, actionArea; readonly GridSplitter splitter;
 List<LibraryItem> all = new(); List<VideoLibraryRow> rows = new(); List<string> warnings = new();
 HashSet<string> productHits = new(); string productQuery = "";
 int generation; bool syncingHeader, applyingPreset, fullscreen; LibraryPreset preset = LibraryPreset.Today;
 (WindowStyle Style, WindowState State, ResizeMode Resize) saved;
 public IReadOnlyList<VideoLibraryRow> Rows => rows;
 public IReadOnlyList<LibraryItem> AllItems => all;
 public bool Loading { get; private set; }
 public bool Busy { get; private set; }
 public LibraryPreset Preset => preset;
 public int LoadVersion { get; private set; }
 public List<VideoLibraryRow> SelectedRows => Grid.SelectedItems.OfType<VideoLibraryRow>().ToList();
 Window Owner => Window.GetWindow(this) ?? Application.Current.MainWindow;

 public VideoLibraryView(IVideoLibraryHost host) {
  this.host = host; Player = new LibraryPlayer(host); Margin = new Thickness(10);
  // ---- search bar
  var top = new StackPanel(); DockPanel.SetDock(top, Dock.Top); Children.Add(top); topArea = top;
  var searchRow = new DockPanel { Margin = new Thickness(0, 0, 0, 6) }; top.Children.Add(searchRow);
  var clear = ActionButton("清空", () => { Query.Clear(); productHits = new(); productQuery = ""; ApplyFilter(); FocusSearch(); return Task.CompletedTask; }); DockPanel.SetDock(clear, Dock.Right); searchRow.Children.Add(clear);
  var refresh = ActionButton("刷新", () => ReloadAsync()); DockPanel.SetDock(refresh, Dock.Right); searchRow.Children.Add(refresh);
  var searchBox = new Grid(); searchBox.Children.Add(Query); searchBox.Children.Add(placeholder); searchRow.Children.Add(searchBox);
  Query.TextChanged += (_, _) => { placeholder.Visibility = Query.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed; debounce.Stop(); debounce.Start(); };
  Query.KeyDown += async (_, e) => { if (e.Key == Key.Enter) { e.Handled = true; debounce.Stop(); await Safe(() => SearchNowAsync(true)); } };
  debounce.Tick += async (_, _) => { debounce.Stop(); await Safe(() => SearchNowAsync(false)); };
  var filterRow = new WrapPanel { Margin = new Thickness(0, 0, 0, 4) }; top.Children.Add(filterRow);
  foreach (var (p, label) in new[] { (LibraryPreset.Today, "今天"), (LibraryPreset.Last7, "近7天"), (LibraryPreset.Last30, "近30天"), (LibraryPreset.All, "全部"), (LibraryPreset.Custom, "自定义") }) {
   var radio = new RadioButton { Content = label, GroupName = "library-date-" + GetHashCode(), Margin = new Thickness(4, 4, 10, 4), VerticalAlignment = VerticalAlignment.Center, IsChecked = p == preset };
   radio.Checked += (_, _) => { if (applyingPreset) return; preset = p; From.IsEnabled = To.IsEnabled = p == LibraryPreset.Custom; ApplyFilter(); };
   presets[p] = radio; filterRow.Children.Add(radio);
  }
  From.IsEnabled = To.IsEnabled = false; From.SelectedDate = To.SelectedDate = DateTime.Today;
  From.SelectedDateChanged += (_, _) => { if (preset == LibraryPreset.Custom) ApplyFilter(); };
  To.SelectedDateChanged += (_, _) => { if (preset == LibraryPreset.Custom) ApplyFilter(); };
  filterRow.Children.Add(From); filterRow.Children.Add(new TextBlock { Text = " 至 ", VerticalAlignment = VerticalAlignment.Center }); filterRow.Children.Add(To);
  filterRow.Children.Add(new TextBlock { Text = "   状态 ", VerticalAlignment = VerticalAlignment.Center });
  StatusBox.ItemsSource = new[] { "全部", "已完成", "在录", "异常", "售后保护", "已归档" }; StatusBox.SelectedIndex = 0;
  StatusBox.SelectionChanged += (_, _) => ApplyFilter(); filterRow.Children.Add(StatusBox); filterRow.Children.Add(Summary);
  // ---- row actions
  var actions = new WrapPanel { Margin = new Thickness(0, 2, 0, 6) }; DockPanel.SetDock(actions, Dock.Top); Children.Add(actions); actionArea = actions;
  actions.Children.Add(ActionButton("播放所选", PlaySelectedAsync));
  actions.Children.Add(ActionButton("删除所选", DeleteSelectedAsync));
  actions.Children.Add(ActionButton("备注", EditNoteAsync));
  actions.Children.Add(ActionButton("售后保护 / 解除", ToggleProtectionAsync));
  actions.Children.Add(ActionButton("后补采购归档", ArchiveAsync));
  actions.Children.Add(new TextBlock { Text = "   清理旧录像：", VerticalAlignment = VerticalAlignment.Center });
  CleanupBox.ItemsSource = new[] { "90天以上", "180天以上" }; CleanupBox.SelectedIndex = host.CleanupDays == 90 ? 0 : 1; actions.Children.Add(CleanupBox);
  actions.Children.Add(ActionButton("选出旧录像", () => { SelectOld(CleanupBox.SelectedIndex == 0 ? 90 : 180); return Task.CompletedTask; }));
  DockPanel.SetDock(Notice, Dock.Bottom); Children.Add(Notice);
  // ---- grid + player
  var body = new Grid(); body.ColumnDefinitions.Add(gridColumn); body.ColumnDefinitions.Add(splitColumn); body.ColumnDefinitions.Add(playerColumn); Children.Add(body);
  BuildGrid(); body.Children.Add(Grid);
  splitter = new GridSplitter { Width = 6, HorizontalAlignment = HorizontalAlignment.Stretch, Background = new SolidColorBrush(Color.FromRgb(220, 230, 234)), ResizeBehavior = GridResizeBehavior.PreviousAndNext, ToolTip = "拖动调整播放器宽度" };
  System.Windows.Controls.Grid.SetColumn(splitter, 1); body.Children.Add(splitter);
  System.Windows.Controls.Grid.SetColumn(Player, 2); body.Children.Add(Player);
  Player.FullscreenRequested += ToggleFullscreen;
  Player.Message += text => Notice.Text = text;
  Loaded += (_, _) => FocusSearch();
  PreviewKeyDown += (_, e) => {
   if (e.Key == Key.Escape && fullscreen) { ToggleFullscreen(); e.Handled = true; }
   else if (e.Key == Key.F5) { e.Handled = true; _ = Safe(() => ReloadAsync()); }
   else if (e.Key == Key.F && Keyboard.Modifiers == ModifierKeys.Control) { e.Handled = true; FocusSearch(); }
  };
 }

 Button ActionButton(string text, Func<Task> action) {
  var b = new Button { Content = text, Margin = new Thickness(3), Padding = new Thickness(12, 6, 12, 6) };
  b.Click += async (_, _) => { if (Busy) return; b.IsEnabled = false; try { await Safe(action); } finally { b.IsEnabled = true; } };
  return b;
 }
 public async Task Safe(Func<Task> action) {
  if (Busy) return; Busy = true;
  try { await action(); }
  catch (Exception ex) { Notice.Text = ex.Message; LibraryDialog.Info(Owner, "操作未完成", ex.Message); }
  finally { Busy = false; }
 }

 void BuildGrid() {
  var g = Grid;
  g.AutoGenerateColumns = false; g.CanUserAddRows = false; g.CanUserDeleteRows = false; g.IsReadOnly = true;
  g.SelectionMode = DataGridSelectionMode.Extended; g.SelectionUnit = DataGridSelectionUnit.FullRow; g.HeadersVisibility = DataGridHeadersVisibility.Column;
  g.EnableRowVirtualization = true; g.EnableColumnVirtualization = true; g.CanUserSortColumns = true; g.CanUserReorderColumns = true;
  VirtualizingPanel.SetIsVirtualizing(g, true); VirtualizingPanel.SetVirtualizationMode(g, VirtualizationMode.Recycling); VirtualizingPanel.SetIsVirtualizingWhenGrouping(g, true);
  ScrollViewer.SetCanContentScroll(g, true); g.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto;
  // Row checkbox == row selection (no separate "Checked" model to drift out of sync).
  var box = new FrameworkElementFactory(typeof(CheckBox));
  box.SetBinding(ToggleButton.IsCheckedProperty, new Binding("IsSelected") { RelativeSource = new RelativeSource(RelativeSourceMode.FindAncestor, typeof(DataGridRow), 1), Mode = BindingMode.TwoWay, UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged });
  box.SetValue(HorizontalAlignmentProperty, HorizontalAlignment.Center); box.SetValue(VerticalAlignmentProperty, VerticalAlignment.Center); box.SetValue(FocusableProperty, false);
  // IsChecked events (not Click) so mouse, keyboard and UI Automation toggles behave the same.
  // From a partial selection a click selects everything; from "all" it clears.
  HeaderCheck.Checked += (_, _) => { if (syncingHeader) return; Grid.SelectAll(); SyncHeader(); };
  HeaderCheck.Unchecked += (_, _) => { if (syncingHeader) return; var n = Grid.SelectedItems.Count; if (n > 0 && n < rows.Count) Grid.SelectAll(); else Grid.UnselectAll(); SyncHeader(); };
  g.Columns.Add(new DataGridTemplateColumn { Header = HeaderCheck, Width = 44, CanUserSort = false, CanUserResize = false, CanUserReorder = false, CellTemplate = new DataTemplate { VisualTree = box } });
  void Text(string header, string binding, string sort, double width) => g.Columns.Add(new DataGridTextColumn { Header = header, Binding = new Binding(binding), SortMemberPath = sort, Width = width, IsReadOnly = true });
  Text("开始时间", nameof(VideoLibraryRow.StartText), nameof(VideoLibraryRow.StartTicks), 150);
  Text("时长", nameof(VideoLibraryRow.DurationText), nameof(VideoLibraryRow.Duration), 70);
  Text("物流", nameof(VideoLibraryRow.Tracking), nameof(VideoLibraryRow.Tracking), 150);
  Text("采购", nameof(VideoLibraryRow.Purchase), nameof(VideoLibraryRow.Purchase), 130);
  Text("外部单号", nameof(VideoLibraryRow.Outer), nameof(VideoLibraryRow.Outer), 130);
  var status = new FrameworkElementFactory(typeof(Border));
  status.SetBinding(Border.BackgroundProperty, new Binding(nameof(VideoLibraryRow.StatusBrush))); status.SetValue(Border.CornerRadiusProperty, new CornerRadius(4)); status.SetValue(Border.PaddingProperty, new Thickness(8, 2, 8, 2)); status.SetValue(HorizontalAlignmentProperty, HorizontalAlignment.Left); status.SetValue(VerticalAlignmentProperty, VerticalAlignment.Center);
  var statusText = new FrameworkElementFactory(typeof(TextBlock)); statusText.SetBinding(TextBlock.TextProperty, new Binding(nameof(VideoLibraryRow.Status))); statusText.SetValue(TextBlock.ForegroundProperty, Brushes.White); status.AppendChild(statusText);
  g.Columns.Add(new DataGridTemplateColumn { Header = "状态", SortMemberPath = nameof(VideoLibraryRow.Status), CanUserSort = true, Width = 76, CellTemplate = new DataTemplate { VisualTree = status } });
  Text("大小", nameof(VideoLibraryRow.SizeText), nameof(VideoLibraryRow.Bytes), 84);
  var prot = new FrameworkElementFactory(typeof(TextBlock)); prot.SetBinding(TextBlock.TextProperty, new Binding(nameof(VideoLibraryRow.Protection))); prot.SetBinding(TextBlock.ForegroundProperty, new Binding(nameof(VideoLibraryRow.ProtectionBrush))); prot.SetValue(VerticalAlignmentProperty, VerticalAlignment.Center);
  g.Columns.Add(new DataGridTemplateColumn { Header = "保护", SortMemberPath = nameof(VideoLibraryRow.Protection), CanUserSort = true, Width = 78, CellTemplate = new DataTemplate { VisualTree = prot } });
  Text("归档", nameof(VideoLibraryRow.Archive), nameof(VideoLibraryRow.Archive), 130);
  // No star-sized column: with one, WPF DataGrid squeezes every fixed column down to fit instead of scrolling.
  Text("备注", nameof(VideoLibraryRow.Note), nameof(VideoLibraryRow.Note), 240);
  foreach (var c in g.Columns.Skip(1)) c.MinWidth = 56;
  var rowStyle = new Style(typeof(DataGridRow)); rowStyle.Setters.Add(new Setter(ToolTipProperty, new Binding(nameof(VideoLibraryRow.Tip)))); g.RowStyle = rowStyle;
  g.SelectionChanged += (_, _) => { SyncHeader(); UpdateSummary(); };
  g.MouseDoubleClick += async (_, e) => {
   if (ItemsControl.ContainerFromElement(g, e.OriginalSource as DependencyObject) is DataGridRow { Item: VideoLibraryRow row }) { e.Handled = true; await Safe(() => PlayAsync(row)); }
  };
  g.PreviewKeyDown += async (_, e) => {
   if (e.Key == Key.Delete) { e.Handled = true; await Safe(DeleteSelectedAsync); }
   else if (e.Key == Key.Enter && g.SelectedItem is VideoLibraryRow row) { e.Handled = true; await Safe(() => PlayAsync(row)); }
  };
 }

 void SyncHeader() {
  syncingHeader = true;
  try { var n = Grid.SelectedItems.Count; HeaderCheck.IsChecked = n == 0 ? false : n == rows.Count ? true : null; }
  finally { syncingHeader = false; }
 }
 void UpdateSummary() {
  var selected = SelectedRows;
  Summary.Text = $"共 {rows.Count} 段 · {Size(rows.Sum(r => r.Bytes))}" + (selected.Count > 0 ? $"　已选 {selected.Count} 段 · {Size(selected.Sum(r => r.Bytes))}" : "");
 }
 public void FocusSearch() { Dispatcher.InvokeAsync(() => { Query.Focus(); Keyboard.Focus(Query); Query.SelectAll(); }, DispatcherPriority.Input); }

 // ---------------------------------------------------------------- loading / filtering
 public async Task OpenAsync(int? selectOldDays = null) {
  if (!selectOldDays.HasValue) { Query.Clear(); productHits = new(); productQuery = ""; StatusBox.SelectedIndex = 0; SetPreset(LibraryPreset.Today, false); }
  await ReloadAsync();
  if (selectOldDays is 90 or 180) { CleanupBox.SelectedIndex = selectOldDays == 90 ? 0 : 1; SelectOld(selectOldDays.Value); }
  FocusSearch();
 }
 public void SetPreset(LibraryPreset value, bool apply = true) {
  applyingPreset = true;
  try { preset = value; presets[value].IsChecked = true; From.IsEnabled = To.IsEnabled = value == LibraryPreset.Custom; }
  finally { applyingPreset = false; }
  if (apply) ApplyFilter();
 }
 public async Task ReloadAsync() {
  var my = ++generation; Loading = true; Notice.Text = "正在读取本机录像索引…";
  try {
   var store = host.Store;
   var (items, warn) = await Task.Run(() => { var list = store.List(); return (list, store.Warnings.ToList()); });
   if (my != generation) return;
   all = items; warnings = warn; ApplyFilter();
   Notice.Text = StatusLine();
   // Purchase links can change in the business system (后补采购); refresh them after the rows are visible.
   try {
    var changed = await host.RefreshAssociations(items);
    if (changed > 0 && my == generation) {
     (items, warn) = await Task.Run(() => { var list = store.List(); return (list, store.Warnings.ToList()); });
     if (my != generation) return;
     all = items; warnings = warn; ApplyFilter(); Notice.Text = StatusLine();
    }
   } catch (Exception ex) { if (my == generation) Notice.Text = StatusLine() + "\n采购关联未刷新（业务服务不可用时仅显示本机索引）：" + ex.Message; }
  } finally { if (my == generation) { Loading = false; LoadVersion++; } }
 }
 string StatusLine() {
  var pending = all.Count(i => i.PendingArchive);
  return $"本机共 {all.Count} 段录像" + (pending > 0 ? $"，{pending} 段后补采购待确认归档" : "") + "。双击行播放；Ctrl / Shift 多选，表头复选框全选当前列表。" + (warnings.Count > 0 ? "\n索引警告：" + string.Join("；", warnings.Take(3)) : "");
 }
 (DateTime? From, DateTime? To) Range() => preset switch {
  LibraryPreset.Today => (DateTime.Today, DateTime.Today),
  LibraryPreset.Last7 => (DateTime.Today.AddDays(-6), DateTime.Today),
  LibraryPreset.Last30 => (DateTime.Today.AddDays(-29), DateTime.Today),
  LibraryPreset.Custom => (From.SelectedDate?.Date, To.SelectedDate?.Date),
  _ => (null, null)
 };
 bool MatchesQuery(LibraryItem i, string q) => q.Length == 0 || i.Matches(q) || productHits.Contains(ProductKey(i.Tracking, i.Segment.Context.SessionId, i.Segment.Context.Source));
 public static string ProductKey(string tracking, long session, string source) => tracking + "|" + session + "|" + source;
 IEnumerable<LibraryItem> Filtered(bool ignoreDate) {
  var q = Query.Text.Trim(); var (from, to) = ignoreDate ? (null, null) : Range(); var status = StatusBox.SelectedItem as string ?? "全部"; var active = host.ActiveVideoId;
  if (from != null && to != null && from > to) (from, to) = (to, from);
  return all.Where(i => (from == null || i.At.LocalDateTime.Date >= from) && (to == null || i.At.LocalDateTime.Date <= to) && MatchesQuery(i, q) && (status == "全部" || new VideoLibraryRow(i, active).InStatus(status)));
 }
 public void ApplyFilter() {
  var keep = Grid.SelectedItems.OfType<VideoLibraryRow>().Select(r => r.Id).ToHashSet();
  var active = host.ActiveVideoId;
  rows = Filtered(false).Select(i => new VideoLibraryRow(i, active)).ToList();
  Grid.ItemsSource = rows;
  if (keep.Count > 0) Grid.SelectRows(rows.Where(r => keep.Contains(r.Id)));
  SyncHeader(); UpdateSummary();
 }
 public Task SearchAsync(bool fromEnter) => SearchNowAsync(fromEnter);
 async Task SearchNowAsync(bool fromEnter) {
  var q = Query.Text.Trim();
  if (q != productQuery) { productHits = new(); productQuery = q; }
  ApplyFilter();
  if (q.Length >= 3) {
   try { var hits = await host.FindByProduct(q); if (Query.Text.Trim() == q) { productHits = hits; ApplyFilter(); } } catch { }
  }
  if (fromEnter && q.Length > 0) {
   if (rows.Count == 0 && preset != LibraryPreset.All && Filtered(true).Any()) { SetPreset(LibraryPreset.All); Notice.Text = $"「{q}」不在当前日期范围内，已自动切换到全部日期。"; }
   if (rows.Count == 1) { Grid.SelectedItem = rows[0]; Grid.ScrollIntoView(rows[0]); }
   Query.SelectAll(); // the next scan replaces the text
  }
 }

 // ---------------------------------------------------------------- actions
 public Task PlaySelectedAsync() {
  var row = Grid.SelectedItem as VideoLibraryRow ?? SelectedRows.FirstOrDefault() ?? throw new InvalidOperationException("请先选中一条录像（双击行也可以直接播放）");
  return PlayAsync(row);
 }
 async Task PlayAsync(VideoLibraryRow row) { Notice.Text = "播放：" + row.Tracking + " · " + row.StartText; await Player.Load(row.Item); }

 public async Task DeleteSelectedAsync() {
  var selected = SelectedRows;
  if (selected.Count == 0) throw new InvalidOperationException("请先勾选或选中要删除的录像（Ctrl / Shift 多选，表头复选框全选当前列表）");
  var plan = new DeletePlan(selected.Where(r => !r.Live && !r.Item.Protected).ToList(), selected.Where(r => !r.Live && r.Item.Protected).ToList(), selected.Where(r => r.Live).ToList());
  var includeProtected = DeleteConfirmDialog.Ask(Owner, plan);
  if (includeProtected == null) { Notice.Text = "已取消删除，没有删除任何文件。"; return; }
  var targets = plan.Normal.Concat(includeProtected == true ? plan.Protected : new()).Select(r => r.Item).ToList();
  if (targets.Count == 0) { Notice.Text = "没有可删除的录像（在录、售后保护片段已跳过）。"; return; }
  var store = host.Store; var include = includeProtected == true;
  var result = await host.Exclusive(active => Task.Run(() => store.DeleteSelected(targets, active, include, true)));
  await host.AfterDelete();
  if (Player.CurrentId != null && targets.Any(t => t.Id == Player.CurrentId) && !File.Exists(targets.First(t => t.Id == Player.CurrentId).File)) await Player.Clear();
  await ReloadAsync();
  var skipped = plan.Live.Count + (include ? 0 : plan.Protected.Count) + result.Skipped.Count;
  Notice.Text = $"已删除 {result.Deleted} 段" + (result.MissingMedia > 0 ? $"（其中 {result.MissingMedia} 段视频早已缺失，已清理索引）" : "") + "。" + (skipped > 0 ? $"跳过 {skipped} 段（在录 / 售后保护 / 状态已变化）。" : "") + (result.Errors.Count > 0 ? "\n未完成：" + string.Join("；", result.Errors) : "");
  if (result.Errors.Count > 0) LibraryDialog.Info(Owner, "部分录像未删除", string.Join("\n", result.Errors.Take(12)));
 }

 public async Task EditNoteAsync() {
  if (!host.NotesEnabled) throw new InvalidOperationException("请在设置 → 视频举证中开启备注功能");
  var selected = SelectedRows; if (selected.Count != 1) throw new InvalidOperationException("备注一次只能编辑一段，请只选中一条录像");
  var row = selected[0]; if (row.Live) throw new InvalidOperationException("当前正在录像，请在监控浮窗中编辑备注，或停录后再编辑");
  var note = PreviewWindow.PromptNote(Owner, row.Item.Note); if (note == null) return;
  var store = host.Store;
  await host.Exclusive(async active => { store.Edit(row.Item, note, row.Item.Protected, active); await host.AfterChange(row.Item); return true; });
  await ReloadAsync(); Notice.Text = "备注已保存：" + row.Tracking;
 }

 public async Task ToggleProtectionAsync() {
  if (!host.ProtectionEnabled) throw new InvalidOperationException("请在设置 → 视频举证中开启售后保护功能（已有保护不会因关闭功能而解除）");
  var selected = SelectedRows.Where(r => !r.Live).ToList();
  if (selected.Count == 0) throw new InvalidOperationException(SelectedRows.Count > 0 ? "正在录像的片段请停录后再设置保护" : "请先选中要设置售后保护的录像（可多选）");
  var protect = selected.Any(r => !r.Item.Protected);
  var targets = protect ? selected.Where(r => !r.Item.Protected).ToList() : selected;
  if (!protect && !LibraryDialog.Confirm(Owner, "确认解除售后保护", $"解除 {targets.Count} 段录像的售后保护？解除后可被删除或进入旧录像清理。\n\n" + string.Join("\n", targets.Take(10).Select(r => r.Tracking + " · " + r.StartText)) + (targets.Count > 10 ? "\n…" : ""), "解除保护")) return;
  var store = host.Store; var errors = new List<string>();
  await host.Exclusive(async active => {
   foreach (var r in targets) { try { store.Edit(r.Item, r.Item.Note, protect, active); await host.AfterChange(r.Item); } catch (Exception ex) { errors.Add(r.Tracking + "：" + ex.Message); } }
   return true;
  });
  await ReloadAsync();
  Notice.Text = (protect ? "已设为售后保护 " : "已解除售后保护 ") + (targets.Count - errors.Count) + " 段。" + (errors.Count > 0 ? "\n未完成：" + string.Join("；", errors) : "");
 }

 public async Task ArchiveAsync() {
  if (host.Recording) throw new InvalidOperationException("请先结束录像，再预览并确认移动归档旧录像");
  var selected = SelectedRows; if (selected.Count != 1) throw new InvalidOperationException("后补采购归档一次处理一段，请只选中一条录像");
  var shown = selected[0].Item; await host.RefreshContext(shown);
  var store = host.Store;
  var item = (await Task.Run(() => store.List())).SingleOrDefault(i => i.Id == shown.Id) ?? throw new InvalidOperationException("录像已不存在，请刷新");
  if (!item.PendingArchive) throw new InvalidOperationException("此段没有待归档的后补采购关联；不会静默搬动其他历史录像");
  var target = store.ArchiveTarget(item);
  if (!LibraryDialog.Confirm(Owner, "确认后补采购归档", "仅移动以下已停录片段及它的索引、日志和照片，视频仍只保存一份。\n\n原路径：\n" + item.File + "\n\n新路径：\n" + target, "确认归档")) return;
  var moved = await host.Exclusive(async active => {
   if (host.Recording) throw new InvalidOperationException("确认期间又开始了录像，归档已取消，请停录后重新预览");
   var path = await Task.Run(() => store.ArchiveConfirmed(item, target, active, true));
   await host.AfterChange(item, path); return path;
  });
  if (Player.CurrentId == item.Id) await Player.Clear();
  await ReloadAsync(); Notice.Text = "已归档，视频未复制：" + moved;
 }

 public int SelectOld(int days) {
  Query.Clear(); productHits = new(); productQuery = ""; StatusBox.SelectedIndex = 0; SetPreset(LibraryPreset.All);
  var now = DateTimeOffset.Now; var active = host.ActiveVideoId;
  var candidates = rows.Where(r => VideoLibrary.CleanupEligible(r.Item, days, now, active)).ToList();
  Grid.SelectRows(candidates); SyncHeader(); UpdateSummary();
  if (candidates.Count > 0) Grid.ScrollIntoView(candidates[0]);
  Notice.Text = candidates.Count == 0 ? $"没有超过 {days} 天、已完成且未保护的录像。" : $"已选中 {candidates.Count} 段超过 {days} 天、已完成且未保护的录像（{Size(candidates.Sum(r => r.Bytes))}）。请核对后点「删除所选」；按住 Ctrl 单击可取消个别。";
  return candidates.Count;
 }

 public bool Fullscreen => fullscreen;
 void ToggleFullscreen() {
  var w = Owner; if (w == null) return;
  fullscreen = !fullscreen; FullscreenLayout(fullscreen);
  if (fullscreen) {
   saved = (w.WindowStyle, w.WindowState, w.ResizeMode);
   w.WindowState = WindowState.Normal; w.WindowStyle = WindowStyle.None; w.ResizeMode = ResizeMode.NoResize; w.WindowState = WindowState.Maximized;
  } else { w.WindowStyle = saved.Style; w.ResizeMode = saved.Resize; w.WindowState = saved.State; }
 }
 void FullscreenLayout(bool on) {
  var vis = on ? Visibility.Collapsed : Visibility.Visible;
  topArea.Visibility = actionArea.Visibility = Notice.Visibility = Grid.Visibility = splitter.Visibility = vis;
  if (on) { gridColumn.MinWidth = 0; gridColumn.Width = new GridLength(0); splitColumn.Width = new GridLength(0); playerColumn.Width = new GridLength(1, GridUnitType.Star); }
  else { gridColumn.Width = new GridLength(1, GridUnitType.Star); gridColumn.MinWidth = 360; splitColumn.Width = new GridLength(6); playerColumn.Width = new GridLength(430); }
  Player.SetFullscreen(on);
 }
 // Called when the hosting window closes: the view is reused by the next window, the WebView2 is not.
 public void Detach() { if (fullscreen) { fullscreen = false; FullscreenLayout(false); } Player.Shutdown(); }
}

public sealed record DeletePlan(List<VideoLibraryRow> Normal, List<VideoLibraryRow> Protected, List<VideoLibraryRow> Live);

public sealed class DeleteConfirmDialog : Window {
 public const string DialogTitle = "确认删除所选录像";
 public CheckBox IncludeProtected { get; } = new() { Margin = new Thickness(0, 8, 0, 4) };
 public Button ConfirmButton { get; } = new() { Padding = new Thickness(14, 7, 14, 7), Margin = new Thickness(6) };
 public Button CancelButton { get; } = new() { Content = "取消", Padding = new Thickness(14, 7, 14, 7), Margin = new Thickness(6), IsCancel = true, IsDefault = true };
 public TextBlock SummaryText { get; } = new() { TextWrapping = TextWrapping.Wrap, FontWeight = FontWeights.SemiBold };
 public TextBox Details { get; } = new() { IsReadOnly = true, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, FontFamily = new FontFamily("Consolas, Microsoft YaHei UI"), MinHeight = 140 };
 readonly DeletePlan plan;
 DeleteConfirmDialog(Window owner, DeletePlan plan) {
  this.plan = plan; Title = DialogTitle; Owner = owner; Width = 620; Height = 480; MinWidth = 480; MinHeight = 360; WindowStartupLocation = WindowStartupLocation.CenterOwner; ShowInTaskbar = false;
  var panel = new DockPanel { Margin = new Thickness(16) };
  var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right }; DockPanel.SetDock(buttons, Dock.Bottom); panel.Children.Add(buttons);
  buttons.Children.Add(ConfirmButton); buttons.Children.Add(CancelButton);
  ConfirmButton.Background = new SolidColorBrush(Color.FromRgb(0xFD, 0xEC, 0xEC)); ConfirmButton.Foreground = new SolidColorBrush(Color.FromRgb(0xB0, 0x1E, 0x1E));
  ConfirmButton.Click += (_, _) => DialogResult = true;
  var head = new StackPanel(); DockPanel.SetDock(head, Dock.Top); panel.Children.Add(head);
  head.Children.Add(SummaryText);
  if (plan.Live.Count > 0) head.Children.Add(new TextBlock { Text = $"跳过 {plan.Live.Count} 段正在录制的录像（始终跳过）。", Margin = new Thickness(0, 6, 0, 0), TextWrapping = TextWrapping.Wrap });
  if (plan.Protected.Count > 0) {
   IncludeProtected.Content = $"同时删除售后保护片段（{plan.Protected.Count} 段，{VideoLibraryView.Size(plan.Protected.Sum(r => r.Bytes))}）";
   head.Children.Add(new TextBlock { Text = "售后保护片段默认跳过。", Margin = new Thickness(0, 6, 0, 0) }); head.Children.Add(IncludeProtected);
   IncludeProtected.Checked += (_, _) => Refresh(); IncludeProtected.Unchecked += (_, _) => Refresh();
  } else IncludeProtected.Visibility = Visibility.Collapsed;
  head.Children.Add(new TextBlock { Text = "明细：", Margin = new Thickness(0, 8, 0, 4) });
  panel.Children.Add(Details); Content = panel; Refresh();
  Loaded += (_, _) => CancelButton.Focus();
 }
 public int TargetCount => plan.Normal.Count + (IncludeProtected.IsChecked == true ? plan.Protected.Count : 0);
 void Refresh() {
  var targets = plan.Normal.Concat(IncludeProtected.IsChecked == true ? plan.Protected : new()).ToList();
  var missing = targets.Count(r => r.Bytes == 0);
  SummaryText.Text = targets.Count == 0 ? "没有可删除的录像：所选录像均在录或受售后保护。" : $"将永久删除 {targets.Count} 段录像（{VideoLibraryView.Size(targets.Sum(r => r.Bytes))}），包括各自的照片、日志和索引，不可撤销。" + (missing > 0 ? $"其中 {missing} 段视频文件已不存在，将只清理索引。" : "");
  ConfirmButton.Content = targets.Count == 0 ? "确认删除" : $"确认删除 {targets.Count} 段"; ConfirmButton.IsEnabled = targets.Count > 0;
  var sb = new StringBuilder();
  foreach (var r in plan.Normal) sb.AppendLine("删除　" + Line(r));
  foreach (var r in plan.Protected) sb.AppendLine((IncludeProtected.IsChecked == true ? "删除　" : "跳过（售后保护）　") + Line(r));
  foreach (var r in plan.Live) sb.AppendLine("跳过（在录）　" + Line(r));
  Details.Text = sb.ToString();
 }
 static string Line(VideoLibraryRow r) => $"{r.Tracking} · {r.StartText} · {r.SizeText}";
 // null = cancelled; otherwise whether protected segments are included.
 public static bool? Ask(Window owner, DeletePlan plan) { var d = new DeleteConfirmDialog(owner, plan); return d.ShowDialog() == true ? d.IncludeProtected.IsChecked == true : null; }
}

public static class LibraryDialog {
 public static bool Confirm(Window owner, string title, string text, string yes) => Show(owner, title, text, yes, "取消") == true;
 public static void Info(Window owner, string title, string text) => Show(owner, title, text, null, "确定");
 static bool? Show(Window owner, string title, string text, string? yes, string no) {
  var w = new Window { Title = title, Owner = owner, Width = 560, SizeToContent = SizeToContent.Height, MaxHeight = 640, ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.CenterOwner, ShowInTaskbar = false };
  var panel = new DockPanel { Margin = new Thickness(16) };
  var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 12, 0, 0) }; DockPanel.SetDock(buttons, Dock.Bottom); panel.Children.Add(buttons);
  if (yes != null) { var ok = new Button { Content = yes, Padding = new Thickness(14, 7, 14, 7), Margin = new Thickness(6) }; ok.Click += (_, _) => w.DialogResult = true; buttons.Children.Add(ok); }
  var cancel = new Button { Content = no, Padding = new Thickness(14, 7, 14, 7), Margin = new Thickness(6), IsCancel = true, IsDefault = true }; buttons.Children.Add(cancel);
  if (yes == null) cancel.Click += (_, _) => w.DialogResult = false;
  panel.Children.Add(new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, MaxHeight = 520, Content = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap } });
  w.Content = panel; w.Loaded += (_, _) => cancel.Focus();
  return w.ShowDialog();
 }
}

// Built-in player. WebView2 <video> (Chromium media stack in the pinned WebView2 runtime the app already ships)
// plays the Recorder's fragmented MP4 (H.264, empty_moov + frag_keyframe) from a local file:// URL and seeks in it;
// screenshots and clip export use the resolved FFmpeg so they work on the original file at full resolution.
public sealed class LibraryPlayer : Grid {
 readonly IVideoLibraryHost host;
 readonly Border surface = new() { Background = Brushes.Black, MinHeight = 180 };
 readonly TextBlock title = new() { FontWeight = FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(2, 0, 2, 6), Text = "未选择录像" };
 readonly TextBlock overlay = new() { Foreground = Brushes.Gainsboro, TextWrapping = TextWrapping.Wrap, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(16), Text = "双击左侧录像开始播放" };
 readonly Grid surfaceGrid = new();
 public Button PlayButton { get; } = new() { Content = "▶ 播放", MinWidth = 76, Margin = new Thickness(2) };
 public Slider Seek { get; } = new() { Minimum = 0, Maximum = 1, IsMoveToPointEnabled = true, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(6, 0, 6, 0) };
 public ComboBox Speed { get; } = new() { Width = 70, Margin = new Thickness(2) };
 public Button FullscreenButton { get; } = new() { Content = "全屏", Margin = new Thickness(2) };
 readonly TextBlock time = new() { VerticalAlignment = VerticalAlignment.Center, MinWidth = 96, TextAlignment = TextAlignment.Center };
 public ListView Cues { get; } = new() { MinHeight = 90 };
 readonly TextBlock cueEmpty = new() { Text = "该录像没有扫码记录", Foreground = Brushes.Gray, Margin = new Thickness(8), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, IsHitTestVisible = false };
 readonly UIElement cuePanel, toolPanel, cueLabel;
 WebView2? web; Task? init; string playerUrl = ""; bool syncing; LibraryItem? item; string lastFolder = Environment.GetFolderPath(Environment.SpecialFolder.MyPictures);
 public event Action? FullscreenRequested; public event Action<string>? Message;
 public string? Source { get; private set; }
 public string? CurrentId => item?.Id;
 public bool Opened { get; private set; }
 public string LastError { get; private set; } = "";
 public double Duration { get; private set; }
 public double Position { get; private set; }
 public double? LastSeek { get; private set; }
 public double? LastSeeked { get; private set; }
 public bool Paused { get; private set; } = true;
 public double Rate { get; private set; } = 1;
 public int CueVersion { get; private set; }

 public LibraryPlayer(IVideoLibraryHost host) {
  this.host = host; Margin = new Thickness(6, 0, 0, 0);
  foreach (var h in new[] { GridLength.Auto, new GridLength(3, GridUnitType.Star), GridLength.Auto, GridLength.Auto, new GridLength(2, GridUnitType.Star), GridLength.Auto }) RowDefinitions.Add(new RowDefinition { Height = h });
  Add(title, 0);
  surfaceGrid.Children.Add(surface); surfaceGrid.Children.Add(overlay); Add(surfaceGrid, 1);
  var controls = new DockPanel { Margin = new Thickness(0, 6, 0, 4) };
  DockPanel.SetDock(PlayButton, Dock.Left); controls.Children.Add(PlayButton);
  DockPanel.SetDock(FullscreenButton, Dock.Right); controls.Children.Add(FullscreenButton);
  DockPanel.SetDock(Speed, Dock.Right); controls.Children.Add(Speed);
  DockPanel.SetDock(time, Dock.Right); controls.Children.Add(time);
  controls.Children.Add(Seek); Add(controls, 2);
  Speed.ItemsSource = new[] { "0.5x", "1x", "2x" }; Speed.SelectedIndex = 1;
  Speed.SelectionChanged += async (_, _) => { Rate = Speed.SelectedIndex switch { 0 => 0.5, 2 => 2, _ => 1 }; await Exec($"player.rate({Rate.ToString(CultureInfo.InvariantCulture)})"); };
  PlayButton.Click += async (_, _) => await TogglePlay();
  FullscreenButton.Click += (_, _) => FullscreenRequested?.Invoke();
  Seek.ValueChanged += async (_, e) => { if (!syncing && item != null && Math.Abs(e.NewValue - Position) > 0.3) await SeekTo(e.NewValue); };
  var label = new TextBlock { Text = "扫码记录（点击跳转到对应时间）", Margin = new Thickness(2, 6, 2, 4), FontWeight = FontWeights.SemiBold }; Add(label, 3); cueLabel = label;
  var view = new GridView();
  foreach (var (header, path, width) in new[] { ("时间", nameof(ScanCue.OffsetText), 60.0), ("商品", nameof(ScanCue.Product), 150.0), ("条码", nameof(ScanCue.Barcode), 110.0), ("数量", nameof(ScanCue.Quantity), 50.0) }) view.Columns.Add(new GridViewColumn { Header = header, DisplayMemberBinding = new Binding(path), Width = width });
  Cues.View = view; VirtualizingPanel.SetIsVirtualizing(Cues, true);
  Cues.SelectionChanged += async (_, _) => { if (Cues.SelectedItem is ScanCue cue) await SeekTo(cue.Offset); };
  Cues.PreviewMouseLeftButtonUp += async (_, e) => { if (ItemsControl.ContainerFromElement(Cues, e.OriginalSource as DependencyObject) is ListViewItem { Content: ScanCue cue } && ReferenceEquals(cue, Cues.SelectedItem)) await SeekTo(cue.Offset); };
  var cueGrid = new Grid(); cueGrid.Children.Add(Cues); cueGrid.Children.Add(cueEmpty); Add(cueGrid, 4); cuePanel = cueGrid;
  var tools = new WrapPanel { Margin = new Thickness(0, 6, 0, 0) }; Add(tools, 5); toolPanel = tools;
  Tool(tools, "截图", SnapshotAsync); Tool(tools, "导出片段", ExportAsync); Tool(tools, "打开所在文件夹", () => { OpenFolder(); return Task.CompletedTask; }); Tool(tools, "用系统播放器打开", () => { OpenExternal(); return Task.CompletedTask; });
  UpdateTime();
 }
 void Add(UIElement e, int row) { SetRow(e, row); Children.Add(e); }
 void Tool(Panel p, string text, Func<Task> action) {
  var b = new Button { Content = text, Margin = new Thickness(3), Padding = new Thickness(10, 5, 10, 5) };
  b.Click += async (_, _) => { b.IsEnabled = false; try { await action(); } catch (Exception ex) { Message?.Invoke(ex.Message); LibraryDialog.Info(Window.GetWindow(this), "操作未完成", ex.Message); } finally { b.IsEnabled = true; } };
  p.Children.Add(b);
 }
 public void SetFullscreen(bool on) { var v = on ? Visibility.Collapsed : Visibility.Visible; cuePanel.Visibility = toolPanel.Visibility = cueLabel.Visibility = title.Visibility = v; FullscreenButton.Content = on ? "退出全屏 (Esc)" : "全屏"; }

 Task EnsureWeb() => init ??= InitWeb();
 async Task InitWeb() {
  var env = await host.WebEnvironment();
  web = new WebView2 { DefaultBackgroundColor = System.Drawing.Color.Black };
  surface.Child = web;
  await web.EnsureCoreWebView2Async(env);
  var core = web.CoreWebView2; var s = core.Settings;
  s.AreDevToolsEnabled = false; s.AreDefaultContextMenusEnabled = false; s.IsStatusBarEnabled = false; s.IsZoomControlEnabled = false; s.AreHostObjectsAllowed = false; s.IsWebMessageEnabled = true;
  Directory.CreateDirectory(host.PlayerFolder);
  await File.WriteAllTextAsync(Path.Combine(host.PlayerFolder, "player.html"), PlayerHtml, new UTF8Encoding(false));
  // The player page and the recordings are both local files (file://). A local page may load local media;
  // nothing else can be navigated to. (WebView2 virtual-host mapping returned MEDIA_ERR_SRC_NOT_SUPPORTED
  // for every video in CI, including VP9 WebM, so it is not used for media.)
  var page = Path.GetFullPath(Path.Combine(host.PlayerFolder, "player.html")); playerUrl = new Uri(page).AbsoluteUri;
  core.NavigationStarting += (_, e) => { if (!(Uri.TryCreate(e.Uri, UriKind.Absolute, out var u) && u.IsFile && Path.GetFullPath(u.LocalPath).Equals(page, StringComparison.OrdinalIgnoreCase))) e.Cancel = true; };
  core.NewWindowRequested += (_, e) => e.Handled = true;
  core.WebMessageReceived += (_, e) => OnMessage(e.WebMessageAsJson);
  var done = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
  void Completed(object? _, CoreWebView2NavigationCompletedEventArgs e) { core.NavigationCompleted -= Completed; done.TrySetResult(e.IsSuccess); }
  core.NavigationCompleted += Completed;
  core.Navigate(playerUrl);
  if (!await done.Task.WaitAsync(TimeSpan.FromSeconds(20))) throw new IOException("播放器页面未能加载");
 }
 public static string VideoUrl(string root, string file) => new Uri(ArchiveLayout.Checked(root, file)).AbsoluteUri;
 async Task Exec(string script) { if (web?.CoreWebView2 == null) return; try { await web.CoreWebView2.ExecuteScriptAsync(script); } catch (Exception ex) { LastError = ex.Message; } }

 public async Task Load(LibraryItem target) {
  var root = host.Store.Root; var file = ArchiveLayout.Checked(root, target.File);
  item = target; Opened = false; LastError = ""; Position = 0; Duration = target.DurationSeconds; LastSeek = null; LastSeeked = null; Paused = true;
  title.Text = $"{target.Tracking} · {target.At.LocalDateTime:yyyy-MM-dd HH:mm:ss}"; title.ToolTip = file;
  Source = VideoUrl(root, file); UpdateTime(); _ = LoadCuesAsync(target);
  if (!File.Exists(file)) { Source = null; Fail("录像文件不存在：" + file); return; }
  await Open(root, () => item?.Id == target.Id);
 }
 async Task Open(string root, Func<bool> current) {
  overlay.Text = "正在打开…"; overlay.Visibility = Visibility.Visible;
  try { await EnsureWeb().WaitAsync(TimeSpan.FromSeconds(30)); } catch (Exception ex) { init = null; Fail("内置播放器不可用（可用「用系统播放器打开」）：" + ex.Message); return; }
  if (!current()) return;
  await Exec($"player.load({JsonSerializer.Serialize(Source)},{Rate.ToString(CultureInfo.InvariantCulture)})");
 }
 // Acceptance diagnostics only: open any file under the recording root (e.g. a VP9 WebM) through the same
 // virtual-host pipeline, and report what this machine's WebView2 can decode.
 internal async Task ProbeAsync(string file) {
  var root = host.Store.Root; file = ArchiveLayout.Checked(root, file);
  item = null; Opened = false; LastError = ""; Position = 0; Source = VideoUrl(root, file); var probe = Source;
  await Open(root, () => Source == probe);
 }
 internal async Task<string> DiagnoseAsync() {
  if (web?.CoreWebView2 == null) return "WebView2 未初始化";
  await web.CoreWebView2.ExecuteScriptAsync("window.__diag=JSON.stringify({version:navigator.userAgent,avcHigh:document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.640028\"'),avcBase:document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"'),mseAvc:!!(window.MediaSource&&MediaSource.isTypeSupported('video/mp4; codecs=\"avc1.640028\"')),vp9:document.createElement('video').canPlayType('video/webm; codecs=\"vp9\"')})");
  var raw = await web.CoreWebView2.ExecuteScriptAsync("window.__diag");
  return JsonSerializer.Deserialize<string>(raw) ?? raw;
 }
 public async Task Clear() {
  item = null; Source = null; Opened = false; Position = 0; Duration = 0; title.Text = "未选择录像"; overlay.Text = "双击左侧录像开始播放"; overlay.Visibility = Visibility.Visible;
  Cues.ItemsSource = null; cueEmpty.Visibility = Visibility.Visible; UpdateTime(); await Exec("player.clear()");
 }
 public void Shutdown() {
  try { web?.Dispose(); } catch { }
  web = null; init = null; surface.Child = null; item = null; Source = null; Opened = false; Position = 0; Duration = 0; Paused = true;
  title.Text = "未选择录像"; overlay.Text = "双击左侧录像开始播放"; overlay.Visibility = Visibility.Visible; Cues.ItemsSource = null; cueEmpty.Visibility = Visibility.Visible; UpdateTime();
 }
 void Fail(string message) { LastError = message; overlay.Text = message; overlay.Visibility = Visibility.Visible; Message?.Invoke(message); }
 public async Task TogglePlay() { if (item == null || !Opened) return; await Exec("player.toggle()"); }
 public async Task SeekTo(double seconds) {
  if (item == null) return;
  seconds = Math.Max(0, Duration > 0 ? Math.Min(seconds, Duration) : seconds);
  LastSeek = seconds; Position = seconds; UpdateTime();
  await Exec($"player.seek({seconds.ToString("0.###", CultureInfo.InvariantCulture)})");
 }
 async Task LoadCuesAsync(LibraryItem target) {
  Cues.ItemsSource = null; cueEmpty.Text = "正在读取扫码记录…"; cueEmpty.Visibility = Visibility.Visible;
  IReadOnlyList<ScanCue> cues; string empty = "该录像没有扫码记录";
  try { cues = await host.LoadCues(target); } catch (Exception ex) { cues = Array.Empty<ScanCue>(); empty = "扫码记录读取失败：" + ex.Message; }
  if (item?.Id != target.Id) return;
  Cues.ItemsSource = cues; cueEmpty.Text = empty; cueEmpty.Visibility = cues.Count == 0 ? Visibility.Visible : Visibility.Collapsed; CueVersion++;
 }
 void OnMessage(string json) {
  try {
   using var doc = JsonDocument.Parse(json); var m = doc.RootElement; var type = m.GetProperty("type").GetString();
   double Num(string name) => m.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : double.NaN;
   switch (type) {
    case "opened": Opened = true; overlay.Visibility = Visibility.Collapsed; var d = Num("duration"); if (double.IsFinite(d) && d > 0) Duration = d; UpdateTime(); break;
    case "time": var t = Num("t"); if (double.IsFinite(t)) Position = t; var dd = Num("d"); if (double.IsFinite(dd) && dd > 0) Duration = dd; Paused = m.TryGetProperty("paused", out var p) && p.GetBoolean(); UpdateTime(); break;
    case "seeked": var st = Num("t"); if (double.IsFinite(st)) { Position = st; LastSeeked = st; } UpdateTime(); break;
    case "play": Paused = false; UpdateTime(); break;
    case "pause": Paused = true; UpdateTime(); break;
    case "error": Opened = false; Fail("内置播放器无法播放此录像（可用「用系统播放器打开」）：" + (m.TryGetProperty("message", out var e) ? e.GetString() : "")); break;
    case "fullscreen": FullscreenRequested?.Invoke(); break;
    case "escape": if (FullscreenButton.Content as string != "全屏") FullscreenRequested?.Invoke(); break;
   }
  } catch { }
 }
 void UpdateTime() {
  syncing = true;
  try { Seek.Maximum = Math.Max(1, Duration); Seek.Value = Math.Min(Position, Seek.Maximum); } finally { syncing = false; }
  time.Text = $"{VideoLibraryView.Clock(Position)} / {VideoLibraryView.Clock(Duration)}";
  PlayButton.Content = Paused ? "▶ 播放" : "⏸ 暂停"; PlayButton.IsEnabled = item != null;
 }

 string RequireFile() { if (item == null) throw new InvalidOperationException("请先在左侧双击一条录像"); var f = ArchiveLayout.Checked(host.Store.Root, item.File); if (!File.Exists(f)) throw new FileNotFoundException("录像文件不存在：" + f); return f; }
 string RequireFfmpeg() { var f = host.Ffmpeg; if (string.IsNullOrWhiteSpace(f) || !Path.IsPathFullyQualified(f) || !File.Exists(f)) throw new FileNotFoundException("未找到 FFmpeg，无法截图 / 导出片段；请在设置 → 视频举证中检查录像引擎"); return f; }
 static string Stamp(double s) => VideoLibraryView.Clock(s).Replace(':', '-');
 public async Task SnapshotAsync() {
  var file = RequireFile(); RequireFfmpeg();
  var dlg = new Microsoft.Win32.SaveFileDialog { Filter = "PNG 图片|*.png", InitialDirectory = lastFolder, FileName = ArchiveLayout.Component(item!.Tracking, 60) + "_" + Stamp(Position) + ".png" };
  if (dlg.ShowDialog(Window.GetWindow(this)) != true) return;
  await SnapshotTo(dlg.FileName); lastFolder = Path.GetDirectoryName(dlg.FileName) ?? lastFolder; Message?.Invoke("已保存截图：" + dlg.FileName);
 }
 public async Task<string> SnapshotTo(string output) {
  var file = RequireFile(); var ffmpeg = RequireFfmpeg(); var at = Position.ToString("0.###", CultureInfo.InvariantCulture);
  var r = await Recorder.Run(ffmpeg, new[] { "-hide_banner", "-loglevel", "error", "-y", "-ss", at, "-i", file, "-frames:v", "1", "-update", "1", output }, 60);
  if (r.Code != 0 || !File.Exists(output)) throw new IOException("截图失败：" + r.Err.Trim());
  return output;
 }
 public async Task ExportAsync() {
  var file = RequireFile(); RequireFfmpeg();
  var range = ClipDialog.Ask(Window.GetWindow(this), Math.Max(0, Position - 5), Duration > 0 ? Math.Min(Duration, Position + 25) : Position + 30, Duration);
  if (range == null) return;
  var dlg = new Microsoft.Win32.SaveFileDialog { Filter = "MP4 视频|*.mp4", InitialDirectory = lastFolder, FileName = ArchiveLayout.Component(item!.Tracking, 60) + "_" + Stamp(range.Value.Start) + "_" + Stamp(range.Value.End) + ".mp4" };
  if (dlg.ShowDialog(Window.GetWindow(this)) != true) return;
  await ExportClipTo(dlg.FileName, range.Value.Start, range.Value.End); lastFolder = Path.GetDirectoryName(dlg.FileName) ?? lastFolder; Message?.Invoke("已导出片段：" + dlg.FileName);
 }
 // Stream copy (no re-encode): the cut snaps to the keyframe at or before the start (about 1 s GOP).
 public async Task<string> ExportClipTo(string output, double start, double end) {
  var file = RequireFile(); var ffmpeg = RequireFfmpeg();
  if (end <= start) throw new ArgumentException("终点必须晚于起点");
  if (Path.GetFullPath(output).Equals(Path.GetFullPath(file), StringComparison.OrdinalIgnoreCase)) throw new IOException("不能覆盖原录像");
  string F(double v) => v.ToString("0.###", CultureInfo.InvariantCulture);
  var r = await Recorder.Run(ffmpeg, new[] { "-hide_banner", "-loglevel", "error", "-y", "-ss", F(start), "-i", file, "-t", F(end - start), "-map", "0:v", "-c", "copy", "-movflags", "+faststart", output }, 180);
  if (r.Code != 0 || !File.Exists(output) || new FileInfo(output).Length == 0) throw new IOException("导出失败：" + r.Err.Trim());
  return output;
 }
 public void OpenFolder() { var f = RequireFile(); Process.Start(new ProcessStartInfo("explorer.exe") { Arguments = "/select,\"" + f + "\"", UseShellExecute = true }); }
 public void OpenExternal() { var f = RequireFile(); Process.Start(new ProcessStartInfo(f) { UseShellExecute = true }); }

 const string PlayerHtml = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>player</title>
<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}video{width:100%;height:100%;object-fit:contain;background:#000;display:block}</style></head>
<body><video id="v" preload="auto" playsinline disablepictureinpicture></video>
<script>
const v=document.getElementById('v');let rate=1,last=0;
function post(o){try{chrome.webview.postMessage(o)}catch(e){}}
window.player={
 load(src,r){rate=r||1;v.src=src;v.load();v.defaultPlaybackRate=rate;v.playbackRate=rate;},
 toggle(){if(v.paused){v.play().catch(e=>post({type:'error',message:String(e)}))}else v.pause()},
 seek(t){try{v.currentTime=t}catch(e){post({type:'error',message:String(e)})}},
 rate(r){rate=r;v.defaultPlaybackRate=r;v.playbackRate=r},
 clear(){v.pause();v.removeAttribute('src');v.load()}
};
v.addEventListener('loadedmetadata',()=>{v.playbackRate=rate;post({type:'opened',duration:isFinite(v.duration)?v.duration:null,width:v.videoWidth,height:v.videoHeight})});
v.addEventListener('error',()=>{if(!v.getAttribute('src'))return;const e=v.error;post({type:'error',code:e?e.code:0,message:e?(e.message||('MediaError '+e.code)):''})});
v.addEventListener('timeupdate',()=>{const n=performance.now();if(n-last<200)return;last=n;post({type:'time',t:v.currentTime,d:isFinite(v.duration)?v.duration:null,paused:v.paused})});
v.addEventListener('seeked',()=>post({type:'seeked',t:v.currentTime}));
v.addEventListener('play',()=>post({type:'play'}));v.addEventListener('pause',()=>post({type:'pause',t:v.currentTime}));
v.addEventListener('dblclick',()=>post({type:'fullscreen'}));v.addEventListener('click',()=>player.toggle());
document.addEventListener('keydown',e=>{if(e.key==='Escape')post({type:'escape'});else if(e.key===' '){e.preventDefault();player.toggle()}else if(e.key==='ArrowRight'){v.currentTime=Math.min(v.currentTime+5,v.duration||1e9)}else if(e.key==='ArrowLeft'){v.currentTime=Math.max(0,v.currentTime-5)}});
</script></body></html>
""";
}

public sealed class ClipDialog : Window {
 readonly TextBox start = new() { Width = 120 }, end = new() { Width = 120 };
 ClipDialog(Window owner, double s, double e, double duration) {
  Title = "导出片段"; Owner = owner; Width = 420; SizeToContent = SizeToContent.Height; ResizeMode = ResizeMode.NoResize; WindowStartupLocation = WindowStartupLocation.CenterOwner; ShowInTaskbar = false;
  var p = new StackPanel { Margin = new Thickness(16) };
  p.Children.Add(new TextBlock { Text = "时间格式 分:秒 或 时:分:秒（也可直接填秒数）。" + (duration > 0 ? "录像总长 " + VideoLibraryView.Clock(duration) + "。" : "") + "\n不重新编码，起点会对齐到之前最近的关键帧（约1秒内）。", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 10) });
  start.Text = VideoLibraryView.Clock(s); end.Text = VideoLibraryView.Clock(e);
  foreach (var (label, box) in new[] { ("起点", start), ("终点", end) }) { var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 3, 0, 3) }; row.Children.Add(new TextBlock { Text = label, Width = 50, VerticalAlignment = VerticalAlignment.Center }); row.Children.Add(box); p.Children.Add(row); }
  var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 10, 0, 0) };
  var ok = new Button { Content = "选择保存位置…", Padding = new Thickness(12, 6, 12, 6), Margin = new Thickness(6), IsDefault = true }; ok.Click += (_, _) => { if (Parse(start.Text) is double a && Parse(end.Text) is double b && b > a) DialogResult = true; else LibraryDialog.Info(this, "时间无效", "请填写有效的起点和终点，终点必须晚于起点。"); };
  buttons.Children.Add(ok); buttons.Children.Add(new Button { Content = "取消", Padding = new Thickness(12, 6, 12, 6), Margin = new Thickness(6), IsCancel = true }); p.Children.Add(buttons); Content = p;
 }
 public static double? Parse(string text) {
  text = text.Trim(); if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var secs)) return secs >= 0 ? secs : null;
  var parts = text.Split(':'); if (parts.Length is < 2 or > 3) return null; double total = 0;
  foreach (var part in parts) { if (!double.TryParse(part, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) || v < 0) return null; total = total * 60 + v; }
  return total;
 }
 public static (double Start, double End)? Ask(Window owner, double s, double e, double duration) { var d = new ClipDialog(owner, s, e, duration); return d.ShowDialog() == true ? (Parse(d.start.Text)!.Value, Parse(d.end.Text)!.Value) : null; }
}

// MainWindow side: the real host (backend, recorder gate, settings) and the window wiring.
public sealed partial class MainWindow : IVideoLibraryHost {
 VideoLibraryView? libraryView;
 readonly TaskCompletionSource<CoreWebView2Environment> webEnvironment = new(TaskCreationOptions.RunContinuationsAsynchronously);
 UIElement VideoLibraryPanel() => libraryView = new VideoLibraryView(this);
 string? ActiveVideoId => rec.Active ? rec.Current?.Id : null;
 VideoLibrary IVideoLibraryHost.Store => Library;
 string? IVideoLibraryHost.ActiveVideoId => ActiveVideoId;
 bool IVideoLibraryHost.Recording => rec.Active;
 bool IVideoLibraryHost.NotesEnabled => settings.NotesEnabled;
 bool IVideoLibraryHost.ProtectionEnabled => settings.ProtectionEnabled;
 int IVideoLibraryHost.CleanupDays => settings.CleanupDays;
 string IVideoLibraryHost.Ffmpeg => rec.Engine;
 string IVideoLibraryHost.PlayerFolder => Path.Combine(root, "library-player");
 Task<CoreWebView2Environment> IVideoLibraryHost.WebEnvironment() => webEnvironment.Task;
 async Task<int> IVideoLibraryHost.RefreshAssociations(IReadOnlyList<LibraryItem> items) {
  if (!backend.Alive) return 0;
  var keys = items.Select(i => i.Segment.Context).Where(c => c.SessionId > 0 && c.BatchId > 0 && c.Source is "普通" or "无采购单" or "联合").Select(c => c.Key).Distinct().ToArray();
  var contexts = new List<VideoContext>();
  foreach (var batch in keys.Chunk(100)) {
   using var response = await backend.Get("/api/native/contexts?keys=" + Uri.EscapeDataString(string.Join('|', batch)));
   foreach (var c in response.RootElement.GetProperty("contexts").EnumerateArray()) contexts.Add(c.Deserialize<VideoContext>(ArchiveLayout.Json)!);
  }
  if (contexts.Count == 0) return 0;
  var store = Library;
  await eventsGate.WaitAsync();
  try { return await Task.Run(() => store.AssociateMany(contexts)); } finally { eventsGate.Release(); }
 }
 async Task IVideoLibraryHost.RefreshContext(LibraryItem item) {
  using var response = await backend.Get("/api/native/context?key=" + Uri.EscapeDataString(item.Segment.BatchKey));
  Library.Associate(response.RootElement.GetProperty("context").Deserialize<VideoContext>(ArchiveLayout.Json)!);
 }
 static int Column(JsonElement columns, string name) { var i = 0; foreach (var c in columns.EnumerateArray()) { if (c.GetString() == name) return i; i++; } return -1; }
 static string Cell(JsonElement row, int index) => index < 0 || index >= row.GetArrayLength() ? "" : row[index].ValueKind == JsonValueKind.Null ? "" : row[index].ToString();
 async Task<HashSet<string>> IVideoLibraryHost.FindByProduct(string query) {
  var hits = new HashSet<string>(); if (!backend.Alive) return hits;
  using var d = await backend.Get("/api/native/export?mode=events&sku=" + Uri.EscapeDataString(query));
  var cols = d.RootElement.GetProperty("columns"); int tracking = Column(cols, "物流单号(发生时)"), sid = Column(cols, "批次ID"), source = Column(cols, "收货来源");
  foreach (var r in d.RootElement.GetProperty("rows").EnumerateArray()) if (long.TryParse(Cell(r, sid), out var s)) hits.Add(VideoLibraryView.ProductKey(Cell(r, tracking), s, Cell(r, source)));
  return hits;
 }
 // Scan cues come from the business scan audit (/api/native/export, 逐次扫码流水) for this tracking
 // number and receiving session, positioned by scan time relative to the recording's first frame.
 async Task<IReadOnlyList<ScanCue>> IVideoLibraryHost.LoadCues(LibraryItem item) {
  var seg = item.Segment; if (seg.FirstFrameAt == null || seg.Context.SessionId <= 0 || !backend.Alive) return Array.Empty<ScanCue>();
  using var d = await backend.Get("/api/native/export?mode=events&tracking=" + Uri.EscapeDataString(item.Tracking));
  var cols = d.RootElement.GetProperty("columns");
  int scan = Column(cols, "扫码时间"), confirmed = Column(cols, "确认/修改时间"), raw = Column(cols, "原始条码"), sku = Column(cols, "商品编码"), name = Column(cols, "商品名称"), qty = Column(cols, "数量变化"), op = Column(cols, "操作"), tracking = Column(cols, "物流单号(发生时)"), sid = Column(cols, "批次ID"), source = Column(cols, "收货来源");
  var first = seg.FirstFrameAt.Value; var end = seg.EndAt ?? DateTimeOffset.Now; var cues = new List<ScanCue>();
  foreach (var r in d.RootElement.GetProperty("rows").EnumerateArray()) {
   if (Cell(r, tracking) != item.Tracking || Cell(r, sid) != seg.Context.SessionId.ToString(CultureInfo.InvariantCulture) || (source >= 0 && Cell(r, source) != seg.Context.Source)) continue;
   var text = Cell(r, scan); if (text == "") text = Cell(r, confirmed);
   if (!DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var local)) continue;
   var at = new DateTimeOffset(local); if (at < first.AddSeconds(-2) || at > end.AddSeconds(2)) continue;
   var change = Cell(r, qty); var action = Cell(r, op);
   cues.Add(new ScanCue(Math.Max(0, (at - first).TotalSeconds), at, Cell(r, sku), Cell(r, name), Cell(r, raw), (action == "delete" ? "删除 " : "") + (change.StartsWith('-') || change == "" ? change : "+" + change)));
  }
  return cues.OrderBy(c => c.Offset).ToList();
 }
 async Task<T> IVideoLibraryHost.Exclusive<T>(Func<string?, Task<T>> action) { await eventsGate.WaitAsync(); try { return await action(ActiveVideoId); } finally { eventsGate.Release(); } }
 async Task IVideoLibraryHost.AfterChange(LibraryItem item, string? movedTo) {
  if (rec.Active || rec.Current?.Id != item.Id) return;
  await rec.RefreshMetadataAsync(movedTo ?? item.File);
  if (movedTo != null) archiveHint = "用户确认归档完成，旧录像未复制成多份。";
 }
 async Task IVideoLibraryHost.AfterDelete() { if (!rec.Active && rec.Current != null && !File.Exists(rec.Current.File)) await rec.RefreshMetadataAsync(rec.Current.File); }
 Task OpenLibraryAsync(bool cleanup) => libraryView!.OpenAsync(cleanup && settings.CleanupDays is 90 or 180 ? settings.CleanupDays : null);
}
