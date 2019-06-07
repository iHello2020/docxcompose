from copy import deepcopy
from datetime import datetime
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.coreprops import CT_CoreProperties
from docxcompose.utils import NS
from docxcompose.utils import xpath
from six import string_types
from six import text_type


CUSTOM_PROPERTY_FMTID = '{D5CDD505-2E9C-101B-9397-08002B2CF9AE}'
CUSTOM_PROPERTY_TYPES = {
    'text': '<vt:lpwstr xmlns:vt="{}"/>'.format(NS['vt']),
    'int': '<vt:i4 xmlns:vt="{}"/>'.format(NS['vt']),
    'bool': '<vt:bool xmlns:vt="{}"/>'.format(NS['vt']),
    'datetime': '<vt:filetime xmlns:vt="{}"/>'.format(NS['vt']),
    'float': '<vt:r8 xmlns:vt="{}"/>'.format(NS['vt']),
}


def value2vt(value):
    if isinstance(value, bool):
        el = parse_xml(CUSTOM_PROPERTY_TYPES['bool'])
        el.text = 'true' if value else 'false'
    elif isinstance(value, int):
        el = parse_xml(CUSTOM_PROPERTY_TYPES['int'])
        el.text = text_type(value)
    elif isinstance(value, float):
        el = parse_xml(CUSTOM_PROPERTY_TYPES['float'])
        el.text = text_type(value)
    elif isinstance(value, datetime):
        el = parse_xml(CUSTOM_PROPERTY_TYPES['datetime'])
        el.text = value.strftime('%Y-%m-%dT%H:%M:%SZ')
    elif isinstance(value, string_types):
        el = parse_xml(CUSTOM_PROPERTY_TYPES['text'])
        el.text = value
    else:
        raise TypeError('Unsupported type {}'.format(type(value)))
    return el


def vt2value(element):
    tag = element.tag.split('}')[-1]
    if tag == 'bool':
        if element.text.lower() == u'true':
            return True
        else:
            return False
    elif tag in ['i1', 'i2', 'i4', 'int', 'ui1', 'ui2', 'ui4', 'uint']:
        return int(element.text)
    elif tag in ['r4', 'r8']:
        return float(element.text)
    elif tag == 'filetime':
        return CT_CoreProperties._parse_W3CDTF_to_datetime(element.text)
    else:
        return element.text


class CustomProperties(object):
    """Custom doc properties stored in ``/docProps/custom.xml``.
       Allows updating of doc properties in a document.
    """
    def __init__(self, doc):
        self.doc = doc
        self._element = None

        try:
            part = doc.part.package.part_related_by(RT.CUSTOM_PROPERTIES)
        except KeyError:
            pass
        else:
            self._element = parse_xml(part.blob)

    def dict(self):
        """Returns a dict with all custom doc properties"""
        if self._element is None:
            return dict()

        props = xpath(self._element, u'.//cp:property')
        return {prop.get('name'): vt2value(prop[0]) for prop in props}

    def get(self, name):
        """Get the value of a property."""
        prop = xpath(
            self._element,
            u'.//cp:property[@name="{}"]'.format(name))
        if prop:
            return vt2value(prop[0][0])

    def set(self, name, value):
        """Set the value of a property."""
        prop = xpath(
            self._element,
            u'.//cp:property[@name="{}"]'.format(name))
        if not prop:
            return self.add(name, value)

        el = prop[0][0]
        new_el = value2vt(value)
        el.getparent().replace(el, new_el)

    def add(self, name, value):
        """Add a property."""
        pids = [int(pid) for pid in xpath(self._element, u'.//cp:property/@pid')]
        pid = max(pids) + 1
        # pids start with 2 !?
        if pid < 2:
            pid = 2
        prop = parse_xml('<cp:property xmlns:cp="{}"/>'.format(NS['cp']))
        prop.set('fmtid', CUSTOM_PROPERTY_FMTID)
        prop.set('name', name)
        prop.set('pid', text_type(pid))
        value_el = value2vt(value)
        prop.append(value_el)
        self._element.append(prop)

    def delete(self, name):
        """Delete a property."""
        prop = xpath(
            self._element,
            u'.//cp:property[@name="{}"]'.format(name))
        if prop:
            prop[0].getparent().remove(prop[0])
            # Renumber pids
            pid = 2
            for prop in self._element:
                prop.set('pid', text_type(pid))
                pid += 1

    def set_properties(self, properties):
        for name, value in properties.items():
            self.set(name, value)

    def update_all(self):
        """Update all the document's doc-properties."""
        for name, value in self.dict().items():
            self.update(name, value)

    def update(self, name, value):
        """Update a property field value."""
        if isinstance(value, bool):
            value = u'Y' if value else u'N'
        elif isinstance(value, datetime):
            value = value.strftime('%x')
        else:
            value = text_type(value)

        # Simple field
        sfield = xpath(
            self.doc.element.body,
            u'.//w:fldSimple[contains(@w:instr, \'DOCPROPERTY "{}"\')]'.format(name))
        if sfield:
            text = xpath(sfield[0], './/w:t')
            if text:
                text[0].text = value

        # Complex field
        cfield = xpath(
            self.doc.element.body,
            u'.//w:instrText[contains(.,\'DOCPROPERTY "{}"\')]'.format(name))
        if cfield:
            w_p = cfield[0].getparent().getparent()
            runs = xpath(
                w_p,
                u'.//w:r[following-sibling::w:r/w:fldChar/@w:fldCharType="end"'
                u' and preceding-sibling::w:r/w:fldChar/@w:fldCharType="separate"]')
            if runs:
                first_w_r = runs[0]
                text = xpath(first_w_r, u'.//w:t')
                if text:
                    text[0].text = value
                # remove any additional text-nodes inside the first run. we
                # update the first text-node only with the full cached
                # docproperty value. if for some reason the initial cached
                # value is split into multiple text nodes we remove any
                # additional node after updating the first node.
                for unnecessary_w_t in text[1:]:
                    first_w_r.remove(unnecessary_w_t)

                # if there are multiple runs between "separate" and "end" they
                # all may contain a piece of the cached docproperty value. we
                # can't reliably handle this situation and only update the
                # first node in the first run with the full cached value. it
                # appears any additional runs with text nodes should then be
                # removed to avoid duplicating parts of the cached docproperty
                # value.
                for w_r in runs[1:]:
                    text = xpath(w_r, u'.//w:t')
                    if text:
                        w_p.remove(w_r)

    def remove_field(self, name):
        """Remove the property field but keep it's value."""

        # Simple field
        sfield = xpath(
            self.doc.element.body,
            u'.//w:fldSimple[contains(@w:instr, \'DOCPROPERTY "{}"\')]'.format(name))
        if sfield:
            sfield = sfield[0]
            parent = sfield.getparent()
            index = list(parent).index(sfield)
            w_r = deepcopy(sfield[0])
            parent.remove(sfield)
            parent.insert(index, w_r)

        # Complex field
        cfield = xpath(
            self.doc.element.body,
            u'.//w:instrText[contains(.,\'DOCPROPERTY "{}"\')]'.format(name))
        if cfield:
            w_p = cfield[0].getparent().getparent()
            # Create list of <w:r> nodes for removal
            # Get all <w:r> nodes between <w:fldChar w:fldCharType="begin"/>
            # and <w:fldChar w:fldCharType="separate"/> including boundaries.
            w_rs = xpath(
                w_p,
                u'.//w:r[following-sibling::w:r/w:fldChar/@w:fldCharType="separate" '
                u'and preceding-sibling::w:r/w:fldChar/@w:fldCharType="begin" '
                u'or self::w:r/w:fldChar/@w:fldCharType="begin" '
                u'or self::w:r/w:fldChar/@w:fldCharType="separate"]')
            # Also include <w:r><w:fldChar w:fldCharType="separate"/></w:r>
            w_rs.extend(xpath(
                w_p, u'.//w:r/w:fldChar[@w:fldCharType="end"]/parent::w:r'))
            for w_r in w_rs:
                w_p.remove(w_r)
