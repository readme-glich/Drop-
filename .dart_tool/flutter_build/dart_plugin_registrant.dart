//
// Generated file. Do not edit.
// This file is generated from template in file `flutter_tools/lib/src/flutter_plugins.dart`.
//

// @dart = 3.13

import 'dart:io'; // flutter_ignore: dart_io_import.
import 'package:android_file_picker/android_file_picker.dart' as android_file_picker;
import 'package:file_picker_darwin/file_picker_darwin.dart' as file_picker_darwin;
import 'package:file_picker_linux/file_picker_linux.dart' as file_picker_linux;
import 'package:file_picker_darwin/file_picker_darwin.dart' as file_picker_darwin;
import 'package:windows_file_picker/windows_file_picker.dart' as windows_file_picker;

@pragma('vm:entry-point')
class _PluginRegistrant {

  @pragma('vm:entry-point')
  static void register() {
    if (Platform.isAndroid) {
      try {
        android_file_picker.FilePickerAndroid.registerWith();
      } catch (err) {
        print(
          '`android_file_picker` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isIOS) {
      try {
        file_picker_darwin.FilePickerDarwin.registerWith();
      } catch (err) {
        print(
          '`file_picker_darwin` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isLinux) {
      try {
        file_picker_linux.FilePickerLinux.registerWith();
      } catch (err) {
        print(
          '`file_picker_linux` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isMacOS) {
      try {
        file_picker_darwin.FilePickerDarwin.registerWith();
      } catch (err) {
        print(
          '`file_picker_darwin` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isWindows) {
      try {
        windows_file_picker.FilePickerWindows.registerWith();
      } catch (err) {
        print(
          '`windows_file_picker` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    }
  }
}
