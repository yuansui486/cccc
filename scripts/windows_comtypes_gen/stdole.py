from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    IPicture, OLE_YSIZE_HIMETRIC, OLE_YPOS_PIXELS, OLE_XPOS_CONTAINER,
    OLE_HANDLE, FONTSTRIKETHROUGH, StdFont, OLE_YSIZE_CONTAINER,
    OLE_XSIZE_HIMETRIC, OLE_OPTEXCLUSIVE, Picture, typelib_path,
    FONTNAME, _check_version, Unchecked, FONTBOLD, Default,
    IPictureDisp, Library, OLE_YSIZE_PIXELS, EXCEPINFO, FontEvents,
    StdPicture, GUID, OLE_ENABLEDEFAULTBOOL, IFont, HRESULT, Checked,
    Gray, FONTITALIC, OLE_XPOS_PIXELS, Monochrome, OLE_XPOS_HIMETRIC,
    OLE_XSIZE_PIXELS, OLE_CANCELBOOL, OLE_YPOS_CONTAINER, Font,
    OLE_XSIZE_CONTAINER, _lcid, DISPMETHOD, CoClass, IDispatch,
    IFontEventsDisp, DISPPROPERTY, IUnknown, FONTUNDERSCORE, VgaColor,
    BSTR, OLE_COLOR, IFontDisp, dispid, IEnumVARIANT, FONTSIZE, Color,
    DISPPARAMS, COMMETHOD, OLE_YPOS_HIMETRIC, VARIANT_BOOL
)


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


__all__ = [
    'IPicture', 'OLE_YSIZE_HIMETRIC', 'OLE_YPOS_PIXELS',
    'OLE_XPOS_CONTAINER', 'Monochrome', 'OLE_XPOS_HIMETRIC',
    'OLE_HANDLE', 'FONTSTRIKETHROUGH', 'LoadPictureConstants',
    'StdFont', 'OLE_YSIZE_CONTAINER', 'OLE_XSIZE_PIXELS',
    'OLE_XSIZE_HIMETRIC', 'OLE_CANCELBOOL', 'OLE_OPTEXCLUSIVE',
    'OLE_YPOS_CONTAINER', 'Picture', 'typelib_path', 'Font',
    'OLE_XSIZE_CONTAINER', 'FONTNAME', 'Unchecked', 'FONTBOLD',
    'Default', 'IPictureDisp', 'Library', 'OLE_YSIZE_PIXELS',
    'IFontEventsDisp', 'FontEvents', 'FONTUNDERSCORE', 'VgaColor',
    'StdPicture', 'OLE_ENABLEDEFAULTBOOL', 'OLE_TRISTATE',
    'OLE_COLOR', 'IFontDisp', 'IFont', 'FONTSIZE', 'Checked', 'Color',
    'Gray', 'FONTITALIC', 'OLE_YPOS_HIMETRIC', 'OLE_XPOS_PIXELS'
]
