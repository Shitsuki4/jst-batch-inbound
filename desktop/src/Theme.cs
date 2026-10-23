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
 <!-- 下拉框（状态 / 清理旧录像 / 倍速）：白底、#E5E7EB 描边、圆角 8，聚焦靛蓝；ToggleButton 透明盖在最上层接点击，保留原生 SelectedIndex/键盘行为 -->
 <Style TargetType="ComboBox"><Setter Property="Background" Value="#FFFFFF"/><Setter Property="Foreground" Value="#1C1D2B"/><Setter Property="BorderBrush" Value="#E5E7EB"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Padding" Value="10,5,26,5"/><Setter Property="MinHeight" Value="32"/><Setter Property="FontFamily" Value="Microsoft YaHei UI, Microsoft YaHei, Segoe UI"/><Setter Property="VerticalContentAlignment" Value="Center"/><Setter Property="Cursor" Value="Hand"/><Setter Property="SnapsToDevicePixels" Value="True"/>
  <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ComboBox"><Grid>
   <Border x:Name="box" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="8"/>
   <Path x:Name="arrow" Data="M0,0 L4.5,4.5 L9,0" Stroke="#737790" StrokeThickness="1.6" HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,1,10,0" IsHitTestVisible="False"/>
   <ContentPresenter Margin="{TemplateBinding Padding}" IsHitTestVisible="False" HorizontalAlignment="Left" VerticalAlignment="{TemplateBinding VerticalContentAlignment}" Content="{TemplateBinding SelectionBoxItem}" ContentTemplate="{TemplateBinding SelectionBoxItemTemplate}" ContentTemplateSelector="{TemplateBinding ItemTemplateSelector}" ContentStringFormat="{TemplateBinding SelectionBoxItemStringFormat}"/>
   <ToggleButton Focusable="False" ClickMode="Press" IsChecked="{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}"><ToggleButton.Template><ControlTemplate TargetType="ToggleButton"><Border Background="Transparent"/></ControlTemplate></ToggleButton.Template></ToggleButton>
   <Popup x:Name="PART_Popup" Placement="Bottom" AllowsTransparency="True" Focusable="False" PopupAnimation="Fade" IsOpen="{Binding IsDropDownOpen, RelativeSource={RelativeSource TemplatedParent}}">
    <Border Margin="0,4,0,0" MinWidth="{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}" MaxHeight="{TemplateBinding MaxDropDownHeight}" Background="#FFFFFF" BorderBrush="#E5E7EB" BorderThickness="1" CornerRadius="8" Padding="4"><ScrollViewer VerticalScrollBarVisibility="Auto"><ItemsPresenter KeyboardNavigation.DirectionalNavigation="Contained"/></ScrollViewer></Border>
   </Popup></Grid>
   <ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#C7C9FB"/></Trigger><Trigger Property="IsDropDownOpen" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#4F46E5"/><Setter TargetName="arrow" Property="Stroke" Value="#4F46E5"/></Trigger><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#4F46E5"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.55"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType="ComboBoxItem"><Setter Property="Padding" Value="8,6"/><Setter Property="Foreground" Value="#1C1D2B"/><Setter Property="Cursor" Value="Hand"/>
  <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ComboBoxItem"><Border x:Name="bd" CornerRadius="6" Background="Transparent" Padding="{TemplateBinding Padding}"><ContentPresenter/></Border><ControlTemplate.Triggers><Trigger Property="IsSelected" Value="True"><Setter Property="Foreground" Value="#4F46E5"/><Setter Property="FontWeight" Value="SemiBold"/></Trigger><Trigger Property="IsHighlighted" Value="True"><Setter TargetName="bd" Property="Background" Value="#EEF0FF"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <!-- 日期选择：同下拉框外观；PART_Root/PART_TextBox/PART_Button/PART_Popup 齐全，SelectedDate/日历弹出行为不变 -->
 <Style TargetType="DatePicker"><Setter Property="Background" Value="#FFFFFF"/><Setter Property="Foreground" Value="#1C1D2B"/><Setter Property="BorderBrush" Value="#E5E7EB"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Padding" Value="10,5,4,5"/><Setter Property="MinHeight" Value="32"/><Setter Property="FontFamily" Value="Microsoft YaHei UI, Microsoft YaHei, Segoe UI"/><Setter Property="IsTodayHighlighted" Value="True"/><Setter Property="SnapsToDevicePixels" Value="True"/>
  <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="DatePicker"><Border x:Name="box" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="8">
   <Grid x:Name="PART_Root" Margin="{TemplateBinding Padding}"><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
    <DatePickerTextBox x:Name="PART_TextBox" Grid.Column="0" Background="Transparent" BorderThickness="0" Foreground="{TemplateBinding Foreground}" VerticalContentAlignment="Center" Focusable="{TemplateBinding Focusable}">
     <DatePickerTextBox.Template><ControlTemplate TargetType="DatePickerTextBox"><Grid><ScrollViewer x:Name="PART_ContentHost" VerticalAlignment="Center" Focusable="False" HorizontalScrollBarVisibility="Hidden" VerticalScrollBarVisibility="Hidden"/><ContentControl x:Name="PART_Watermark" Opacity="0" Focusable="False" IsHitTestVisible="False"/></Grid></ControlTemplate></DatePickerTextBox.Template>
    </DatePickerTextBox>
    <Button x:Name="PART_Button" Grid.Column="1" Focusable="False" Width="24" Margin="2,0,0,0" Cursor="Hand"><Button.Template><ControlTemplate TargetType="Button"><Border x:Name="ic" Background="Transparent" CornerRadius="6"><Canvas Width="14" Height="14" HorizontalAlignment="Center" VerticalAlignment="Center"><Rectangle Width="13" Height="11" Canvas.Left="0.5" Canvas.Top="2.5" RadiusX="2" RadiusY="2" Stroke="#737790" StrokeThickness="1.3"/><Line X1="0.5" Y1="6" X2="13.5" Y2="6" Stroke="#737790" StrokeThickness="1.3"/><Line X1="4" Y1="0.5" X2="4" Y2="3.5" Stroke="#737790" StrokeThickness="1.3"/><Line X1="10" Y1="0.5" X2="10" Y2="3.5" Stroke="#737790" StrokeThickness="1.3"/></Canvas></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="ic" Property="Background" Value="#EEF0FF"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Button.Template></Button>
    <Popup x:Name="PART_Popup" Grid.Column="0" AllowsTransparency="True" Placement="Bottom" PlacementTarget="{Binding ElementName=PART_TextBox}" StaysOpen="False"/>
   </Grid></Border>
   <ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#C7C9FB"/></Trigger><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#4F46E5"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter TargetName="box" Property="Background" Value="#F7F8FC"/><Setter Property="Opacity" Value="0.6"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
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
