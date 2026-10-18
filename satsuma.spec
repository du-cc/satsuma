%{!?python3_sitelib: %global python3_sitelib %(python3 -c "import sysconfig; print(sysconfig.get_path('purelib'))")}

Name:           satsuma
Version:        1.0.0
Release:        1%{?dist}
Summary:        Hotkey wheel file converter for GNOME
License:        MIT
URL:            https://github.com/
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita
Requires:       gstreamer1-plugins-base
Requires:       ffmpeg-free
Requires:       ImageMagick
Requires:       poppler-utils
Requires:       pandoc
Requires:       qpdf
Requires:       ghostscript
Requires:       perl-Image-ExifTool
Requires:       librsvg2-tools
Requires:       libheif-tools
Recommends:     libreoffice-writer
Recommends:     libreoffice-calc
Recommends:     libreoffice-impress
Recommends:     nautilus-python
Recommends:     libavif-tools
Recommends:     python3-img2pdf
Recommends:     7zip
Suggests:       tesseract
Suggests:       python3-opencv

%description
Satsuma converts, compresses and edits files from a radial wheel. Select files in
GNOME Files and press F4: the wheel pops up at the pointer with conversions
(Shift), tools (Alt) and actions (Ctrl). Everything runs locally using FFmpeg,
ImageMagick, LibreOffice, Pandoc and Poppler.

%prep
%autosetup

%build

%install
install -d %{buildroot}%{python3_sitelib}
cp -r satsuma %{buildroot}%{python3_sitelib}/
find %{buildroot}%{python3_sitelib}/satsuma -name __pycache__ -prune -exec rm -rf {} +
install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/satsuma <<'LAUNCHER'
#!/usr/bin/python3
import sys
from satsuma.__main__ import main
sys.exit(main())
LAUNCHER
chmod 755 %{buildroot}%{_bindir}/satsuma
install -Dm644 satsuma/data/io.github.satsuma.Satsuma.desktop %{buildroot}%{_datadir}/applications/io.github.satsuma.Satsuma.desktop
install -Dm644 satsuma/data/icons/hicolor/scalable/apps/io.github.satsuma.Satsuma.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.satsuma.Satsuma.svg
install -Dm644 satsuma/data/icons/hicolor/symbolic/apps/io.github.satsuma.Satsuma-symbolic.svg %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps/io.github.satsuma.Satsuma-symbolic.svg
install -d %{buildroot}%{_datadir}/nautilus-python/extensions %{buildroot}%{_datadir}/dbus-1/services
PYTHONPATH=. python3 -c "
from satsuma import integration as I
open('%{buildroot}%{_datadir}/nautilus-python/extensions/satsuma_nautilus.py', 'w').write(I.render_extension('%{_bindir}/satsuma', '%{python3_sitelib}'))
open('%{buildroot}%{_datadir}/dbus-1/services/io.github.satsuma.Satsuma.service', 'w').write(I.render_service('%{_bindir}/satsuma'))
"

%files
%license LICENSE
%doc README.md
%{_bindir}/satsuma
%{python3_sitelib}/satsuma/
%{_datadir}/applications/io.github.satsuma.Satsuma.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.satsuma.Satsuma.svg
%{_datadir}/icons/hicolor/symbolic/apps/io.github.satsuma.Satsuma-symbolic.svg
%{_datadir}/nautilus-python/extensions/satsuma_nautilus.py
%{_datadir}/dbus-1/services/io.github.satsuma.Satsuma.service

%post
echo "Run 'satsuma install-integration' once per user to enable the F4 wheel in Files."

%changelog
* Sun Oct 18 2026 Satsuma contributors - 1.0.0-1
- First release
