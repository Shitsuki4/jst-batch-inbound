using System.Windows;
using System.Windows.Markup;
namespace Receiving.Native;
public static class Theme {
 public static void Apply(){var dictionary=(ResourceDictionary)XamlReader.Parse("""
<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
 <Style TargetType="Button">
  <Setter Property="Background" Value="#ECF7F6"/><Setter Property="Foreground" Value="#125F5C"/>
  <Setter Property="BorderBrush" Value="#B6D9D6"/><Setter Property="BorderThickness" Value="1"/>
  <Setter Property="Padding" Value="13,8"/><Setter Property="Cursor" Value="Hand"/>
  <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button"><Border x:Name="box" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="6"><ContentPresenter Margin="{TemplateBinding Padding}" HorizontalAlignment="Center" VerticalAlignment="Center"/></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="box" Property="Background" Value="#D4EEEA"/></Trigger><Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#007D73"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.5"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter>
 </Style>
 <Style TargetType="TextBox"><Setter Property="Padding" Value="8,6"/><Setter Property="BorderBrush" Value="#C5D3DC"/><Setter Property="VerticalContentAlignment" Value="Center"/></Style>
 <Style TargetType="ComboBox"><Setter Property="Padding" Value="8,6"/></Style>
 <Style TargetType="TabItem">
 <Setter Property="Padding" Value="22,13"/><Setter Property="FontWeight" Value="SemiBold"/><Setter Property="Foreground" Value="#64748B"/><Setter Property="Background" Value="#F2F6F8"/>
 <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="TabItem"><Border x:Name="tab" CornerRadius="8,8,0,0" Background="{TemplateBinding Background}" Margin="3,3,3,0"><ContentPresenter ContentSource="Header" Margin="{TemplateBinding Padding}"/></Border><ControlTemplate.Triggers><Trigger Property="IsSelected" Value="True"><Setter TargetName="tab" Property="Background" Value="White"/><Setter Property="Foreground" Value="#087F78"/></Trigger><Trigger Property="IsMouseOver" Value="True"><Setter Property="Foreground" Value="#087F78"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType="TabControl"><Setter Property="BorderThickness" Value="0"/><Setter Property="Background" Value="White"/><Setter Property="Padding" Value="8"/></Style>
 <Style TargetType="DataGrid"><Setter Property="Background" Value="White"/><Setter Property="AlternatingRowBackground" Value="#F2F8F8"/><Setter Property="RowHeight" Value="34"/><Setter Property="GridLinesVisibility" Value="Horizontal"/><Setter Property="HorizontalGridLinesBrush" Value="#E0E9EC"/><Setter Property="BorderBrush" Value="#D2DFE5"/></Style>
 <Style TargetType="DataGridColumnHeader"><Setter Property="Background" Value="#E3F0F0"/><Setter Property="Padding" Value="10"/><Setter Property="FontWeight" Value="SemiBold"/></Style>
</ResourceDictionary>
""");Application.Current.Resources.MergedDictionaries.Add(dictionary);}
}
