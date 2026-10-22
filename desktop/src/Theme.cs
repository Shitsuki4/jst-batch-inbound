using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
namespace Receiving.Native;
// 方向 C：所有原生窗口共用的控件样式（与网页 C 风格同源 tokens，见下方 Palette）。
// 按钮层级：默认 = secondary（白底描边）；Btn.Primary 主操作；Btn.Ghost 次要/工具；Btn.DangerSoft 列表里的删除入口；Btn.Danger 仅用于删除确认。
public static class Theme {
 public const string Primary="Btn.Primary",Secondary="Btn.Secondary",Ghost="Btn.Ghost",Danger="Btn.Danger",DangerSoft="Btn.DangerSoft";
 public static void Apply(){var dictionary=(ResourceDictionary)XamlReader.Parse("""
<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
 <Style TargetType="Button">
  <Setter Property="Background" Value="#FFFFFF"/><Setter Property="Foreground" Value="#1C1D2B"/>
  <Setter Property="BorderBrush" Value="#D4D6E2"/><Setter Property="BorderThickness" Value="1"/>
  <Setter Property="Padding" Value="14,7"/><Setter Property="Cursor" Value="Hand"/>
  <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button"><Border x:Name="box" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="10" SnapsToDevicePixels="True"><Grid><Border x:Name="hover" CornerRadius="9" Background="#1C1D2B" Opacity="0"/><ContentPresenter Margin="{TemplateBinding Padding}" HorizontalAlignment="Center" VerticalAlignment="Center" TextElement.FontWeight="SemiBold"/></Grid></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="hover" Property="Opacity" Value="0.06"/></Trigger><Trigger Property="IsPressed" Value="True"><Setter TargetName="hover" Property="Opacity" Value="0.12"/></Trigger><Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#4F46E5"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.45"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter>
 </Style>
 <Style x:Key="Btn.Secondary" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}"/>
 <Style x:Key="Btn.Primary" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}"><Setter Property="Background" Value="#4F46E5"/><Setter Property="Foreground" Value="#FFFFFF"/><Setter Property="BorderBrush" Value="#4F46E5"/></Style>
 <Style x:Key="Btn.Ghost" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}"><Setter Property="Background" Value="Transparent"/><Setter Property="Foreground" Value="#5D6075"/><Setter Property="BorderBrush" Value="Transparent"/></Style>
 <Style x:Key="Btn.Danger" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}"><Setter Property="Background" Value="#E11D48"/><Setter Property="Foreground" Value="#FFFFFF"/><Setter Property="BorderBrush" Value="#E11D48"/></Style>
 <Style x:Key="Btn.DangerSoft" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}"><Setter Property="Background" Value="#FFE9EE"/><Setter Property="Foreground" Value="#9F1239"/><Setter Property="BorderBrush" Value="#FBC4D0"/></Style>
 <Style TargetType="TextBox"><Setter Property="Padding" Value="8,6"/><Setter Property="Background" Value="#FFFFFF"/><Setter Property="Foreground" Value="#1C1D2B"/><Setter Property="BorderBrush" Value="#D4D6E2"/><Setter Property="BorderThickness" Value="1"/><Setter Property="VerticalContentAlignment" Value="Center"/>
  <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="TextBox"><Border x:Name="box" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="10" SnapsToDevicePixels="True"><ScrollViewer x:Name="PART_ContentHost" Margin="{TemplateBinding Padding}" VerticalAlignment="{TemplateBinding VerticalContentAlignment}" Focusable="False" HorizontalScrollBarVisibility="Hidden" VerticalScrollBarVisibility="Hidden"/></Border><ControlTemplate.Triggers><Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#4F46E5"/></Trigger><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#A5A8C4"/></Trigger><Trigger Property="IsReadOnly" Value="True"><Setter Property="Background" Value="#F7F8FC"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.55"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType="ComboBox"><Setter Property="Padding" Value="8,6"/><Setter Property="BorderBrush" Value="#D4D6E2"/><Setter Property="Background" Value="#FFFFFF"/><Setter Property="Foreground" Value="#1C1D2B"/></Style>
 <Style TargetType="DatePicker"><Setter Property="BorderBrush" Value="#D4D6E2"/><Setter Property="Padding" Value="4,3"/></Style>
 <Style TargetType="RadioButton"><Setter Property="Foreground" Value="#1C1D2B"/></Style>
 <Style TargetType="CheckBox"><Setter Property="Foreground" Value="#1C1D2B"/></Style>
 <Style TargetType="TabItem">
 <Setter Property="Padding" Value="22,13"/><Setter Property="FontWeight" Value="SemiBold"/><Setter Property="Foreground" Value="#5D6075"/><Setter Property="Background" Value="#F3F4F9"/>
 <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="TabItem"><Border x:Name="tab" CornerRadius="10,10,0,0" Background="{TemplateBinding Background}" Margin="3,3,3,0"><ContentPresenter ContentSource="Header" Margin="{TemplateBinding Padding}"/></Border><ControlTemplate.Triggers><Trigger Property="IsSelected" Value="True"><Setter TargetName="tab" Property="Background" Value="White"/><Setter Property="Foreground" Value="#4F46E5"/></Trigger><Trigger Property="IsMouseOver" Value="True"><Setter Property="Foreground" Value="#4F46E5"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType="TabControl"><Setter Property="BorderThickness" Value="0"/><Setter Property="Background" Value="White"/><Setter Property="Padding" Value="8"/></Style>
 <Style TargetType="DataGrid"><Setter Property="Background" Value="White"/><Setter Property="RowBackground" Value="White"/><Setter Property="AlternatingRowBackground" Value="#FAFAFD"/><Setter Property="RowHeight" Value="34"/><Setter Property="GridLinesVisibility" Value="Horizontal"/><Setter Property="HorizontalGridLinesBrush" Value="#EEEFF5"/><Setter Property="BorderBrush" Value="#E7E8F0"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Foreground" Value="#1C1D2B"/></Style>
 <Style TargetType="DataGridColumnHeader"><Setter Property="Background" Value="#F7F8FC"/><Setter Property="Foreground" Value="#5D6075"/><Setter Property="Padding" Value="10,8"/><Setter Property="FontWeight" Value="SemiBold"/><Setter Property="BorderBrush" Value="#E7E8F0"/><Setter Property="BorderThickness" Value="0,0,1,1"/></Style>
 <Style TargetType="DataGridCell"><Setter Property="BorderThickness" Value="0"/><Setter Property="Padding" Value="6,0"/><Style.Triggers><Trigger Property="IsSelected" Value="True"><Setter Property="Background" Value="#EEF0FF"/><Setter Property="Foreground" Value="#1C1D2B"/><Setter Property="BorderBrush" Value="#EEF0FF"/></Trigger></Style.Triggers></Style>
 <Style TargetType="ListView"><Setter Property="Background" Value="White"/><Setter Property="BorderBrush" Value="#E7E8F0"/><Setter Property="Foreground" Value="#1C1D2B"/></Style>
 <Style TargetType="GridViewColumnHeader"><Setter Property="Background" Value="#F7F8FC"/><Setter Property="Foreground" Value="#5D6075"/><Setter Property="Padding" Value="8,6"/><Setter Property="FontWeight" Value="SemiBold"/><Setter Property="HorizontalContentAlignment" Value="Left"/></Style>
 <Style TargetType="GridSplitter"><Setter Property="Background" Value="Transparent"/></Style>
</ResourceDictionary>
""");Application.Current.Resources.MergedDictionaries.Add(dictionary);}
 // Button level by resource reference: falls back to the implicit (secondary) style if Apply() did not run.
 public static T Level<T>(this T button,string level) where T:Button {button.SetResourceReference(FrameworkElement.StyleProperty,level);return button;}
 // Shared dialog chrome: page background, YaHei UI, C text color.
 public static T Dialog<T>(T window) where T:Window {window.Background=Palette.Bg;window.FontFamily=Palette.Font;window.FontSize=14;window.Foreground=Palette.Text;return window;}
 // White panel, 1px line, radius 12.
 public static Border Card(UIElement? child,double padding=12)=>new(){Background=Palette.Surface,BorderBrush=Palette.Line,BorderThickness=new Thickness(1),CornerRadius=new CornerRadius(Palette.Radius),Padding=new Thickness(padding),Child=child,SnapsToDevicePixels=true};
 public static TextBlock Label(string text)=>new(){Text=text,Foreground=Palette.Text2,VerticalAlignment=VerticalAlignment.Center};
}

// 方向 C tokens（与网页 C 风格同源）。录像浮窗、主窗口外壳、录像查询与原生对话框共用。
public static class Palette {
 static SolidColorBrush B(string hex){var b=new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));b.Freeze();return b;}
 public static readonly SolidColorBrush Surface=B("#FFFFFF"),Bg=B("#F3F4F9"),Dark=B("#191A2E"),Surface2=B("#F7F8FC"),Line=B("#E7E8F0"),LineStrong=B("#D4D6E2"),
  Text=B("#1C1D2B"),Text2=B("#5D6075"),Text3=B("#737790"),
  Brand=B("#4F46E5"),BrandHover=B("#4338CA"),BrandSoft=B("#EEF0FF"),BrandLine=B("#C7C9FB"),
  Ok=B("#0F9D6B"),OkSoft=B("#E5F7F0"),Warn=B("#D97706"),WarnSoft=B("#FEF4E3"),WarnText=B("#92400E"),
  Danger=B("#E11D48"),DangerSoft=B("#FFE9EE"),DangerText=B("#9F1239"),
  Rec=B("#FF4D73"),Idle=B("#9A9DB4"),Grip=B("#B9BBCD"),
  Stage=B("#14152A"),StageScrim=B("#7314152A"),Glass=B("#9914152A"),OnDark=B("#FFFFFF"),OnDark2=B("#D6D7EA"),OnDarkLine=B("#59FFFFFF");
 public static readonly FontFamily Font=new("Microsoft YaHei UI, Microsoft YaHei, Segoe UI");
 public static readonly FontFamily Mono=new("Cascadia Mono, Consolas, Microsoft YaHei UI");
 public const double Radius=12, RadiusSm=8;
}
