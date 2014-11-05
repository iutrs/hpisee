#!/usr/bin/env python
#
# Simple proof of concept implementation of an HP ISEE (Instant
# Support Enterprise Edition) client. It supports client registration
# and entitlement/warranty lookup.
#
# Usage:   $0 serial,prod,[country] [serial,prod,[country] ...]
# Example: $0 CZ10130050,519841-425,ES
#
# Notes:
# * No WSDL available. SOAP protocol forged with lxml+requests.
# * Output is raw XML.
# * Product number is mandatory (unfortunately).
# * Product number must be complete (not only the six first digits),
#   but see also examples/warranty-empty_prodno.xml.
# * Country defaults to 'US'. Results return fine, but with a warning
#   that the country doesn't match shipping country.
# * The template XML files have been stripped to their bare minimum
#   accepted by the server.
#
# OCDnix 2013
# rot13(bpqavk_lnubb.pbz)
# http://ocdnix.wordpress.com/
#

import os
import sys
import json
import requests
from lxml import etree

BASEDIR = os.path.dirname(os.path.realpath(__file__))
TEMPLATES = os.path.join(BASEDIR, 'templates')

def reg_timestamp(payload):
    """
    Add timestamps to registration payload.

    The OSID and CSID timestamps are required for the registration to
    succeed.
    """
    from time import gmtime, strftime
    payload.xpath('/isee:ISEE-Registration/RegistrationSource/' \
            'HP_OOSIdentifiers/OSID/Section/' \
            'Property[@name="TimestampGenerated"]',
            namespaces=reqnsmap)[0].set('value',
                strftime("%Y/%m/%d %H:%M:%S %Z", gmtime()))
    payload.xpath('/isee:ISEE-Registration/RegistrationSource/' \
            'HP_OOSIdentifiers/CSID/Section/' \
            'Property[@name="TimestampGenerated"]',
            namespaces=reqnsmap)[0].set('value',
                strftime("%Y/%m/%d %H:%M:%S %Z", gmtime()))

def reg_addpayload(soapenv, payload):
    """Add registration XML payload to SOAP envelope."""
    soapenv.xpath('/SOAP-ENV:Envelope/SOAP-ENV:Body/' \
            'iseeReg:RegisterClient2/iseeReg:request',
            namespaces=reqnsmap)[0].text = etree.tostring(payload)

def reg_getauthdata(soapenv):
    """Get auth gdid and token fram XML payload."""
    success = soapenv.xpath('/soap:Envelope/soap:Body/' \
            'isee:RegisterClient2Response/' \
            'isee:RegisterClient2Result/' \
            'isee:IsSuccess',
            namespaces=resnsmap)[0].text
    if not success.lower() == 'true':
        error = soapenv.xpath('/soap:Envelope/soap:Body/' \
                'isee:RegisterClient2Response/' \
                'isee:RegisterClient2Result/' \
                'isee:Error',
                namespaces=resnsmap)[0].text
        sys.stderr.write(etree.tostring(error, pretty_print=True))
        sys.exit(1) # FIXME: Don't exit this deep.

    gdid = soapenv.xpath('/soap:Envelope/soap:Body/' \
            'isee:RegisterClient2Response/' \
            'isee:RegisterClient2Result/' \
            'isee:Gdid',
            namespaces=resnsmap)[0].text
    regtoken = soapenv.xpath('/soap:Envelope/soap:Body/' \
            'isee:RegisterClient2Response/' \
            'isee:RegisterClient2Result/' \
            'isee:RegistrationToken',
            namespaces=resnsmap)[0].text

    assert len(gdid)
    assert len(regtoken)
    config['auth']['gdid'] = gdid
    config['auth']['regtoken'] = regtoken
    try:
        authfile = open(config['auth']['file'], 'w')
        json.dump({'gdid': gdid, 'regtoken': regtoken}, authfile, indent=4)
        authfile.close()
    except KeyError:
        return config['auth']  # return auth in-case script imported.

def war_populate(payload):
    """
    Add entitlement parameters.

    Populate the CountryCode, SerialNumber and ProductNumber fields in
    the entitlement info request payload.
    """
    for serial, prodno, country in config['entitlements']:
        assert len(serial) # Required
        #assert len(prodno) # Required
        if not country:
            # Country mismatch is normally allowed.
            country = 'US'

        parent = payload.xpath('/isee:ISEE-GetOOSEntitlementInfoRequest',
                namespaces=reqnsmap_ent)[0]
        entparams = etree.SubElement(parent, 'HP_ISEEEntitlementParameters')
        countrycode = etree.SubElement(entparams, 'CountryCode')
        serialnumber = etree.SubElement(entparams, 'SerialNumber')
        productnumber = etree.SubElement(entparams, 'ProductNumber')
        etree.SubElement(entparams, 'EntitlementType')  # Required
        etree.SubElement(entparams, 'EntitlementId')    # Required
        etree.SubElement(entparams, 'ObligationId')     # Required

        countrycode.text = country
        serialnumber.text = serial
        productnumber.text = prodno

def war_addpayload(soapenv, payload):
    """Add auth and registration XML payload to SOAP envelope."""
    soapenv.xpath('/SOAP-ENV:Envelope/SOAP-ENV:Header/'\
            'isee:IseeWebServicesHeader/isee:GDID',
            namespaces=reqnsmap_war)[0].text = config['auth']['gdid']
    soapenv.xpath('/SOAP-ENV:Envelope/SOAP-ENV:Header/'\
            'isee:IseeWebServicesHeader/isee:registrationToken',
            namespaces=reqnsmap_war)[0].text = config['auth']['regtoken']
    soapenv.xpath('/SOAP-ENV:Envelope/SOAP-ENV:Body/' \
            'isee:GetOOSEntitlementList2/isee:request',
            namespaces=reqnsmap_war)[0].text = etree.tostring(payload)

def war_getentdata(soapenv):
    """Handle entitlement XML payload."""
    # FIXME: Error handling.
    payload = soapenv.xpath('/soap:Envelope/soap:Body/' \
            'isee:GetOOSEntitlementList2Response/' \
            'isee:GetOOSEntitlementList2Result/' \
            'isee:Response',
            namespaces=resnsmap)[0].text
    pltree = etree.fromstring(payload.encode('utf-8'))
    return pltree

# Keep all config in a large structure.
config = {
    'http': {
        'host': 'https://services.isee.hp.com',
        'user-agent': 'RemoteSupport/A.05.05 - gSOAP/2.7',
        'content-type': 'text/xml; charset=utf-8',
    },
    'ops': {
        'register': {
            'url': '/ClientRegistration/ClientRegistrationService.asmx',
            'soap_action': '"http://www.hp.com/isee/webservices/'\
                           'RegisterClient2"',
            'xml_soapenv': os.path.join(TEMPLATES, 'register_soapenv.xml'),
            'xml_payload': os.path.join(TEMPLATES, 'register_payload.xml'),
            'hooks_req_payload': [reg_timestamp],
            'hooks_req_soapenv': [reg_addpayload],
            'hooks_res_soapenv': [reg_getauthdata],
            'hooks_res_payload': [],
        },
        'warranty': {
            'url': '/EntitlementCheck/EntitlementCheckService.asmx',
            'soap_action': '"http://www.hp.com/isee/webservices/'\
                           'GetOOSEntitlementList2"',
            'xml_soapenv': os.path.join(TEMPLATES, 'warranty_soapenv.xml'),
            'xml_payload': os.path.join(TEMPLATES, 'warranty_payload.xml'),
            'hooks_req_payload': [war_populate],
            'hooks_req_soapenv': [war_addpayload],
            'hooks_res_soapenv': [war_getentdata],
            'hooks_res_payload': [],
        },
    },
    'auth': {},         # Added in main().
    'entitlements': [], # Added in main().
}

# Namespace maps. We use .. four since there are duplicates and
# discrepancies between each type of query, and between the client
# request and server response. (Client might use 'SOAP-ENV' while the
# server uses 'soap'.) This is a bloody mess and should be cleaned up
# properly.
reqnsmap = {
    'SOAP-ENV': 'http://schemas.xmlsoap.org/soap/envelope/',
    'iseeReg':  'http://www.hp.com/isee/webservices/',
    'isee':     'http://www.hp.com/schemas/isee/5.00/event',
}
reqnsmap_ent = {
    'SOAP-ENV': 'http://schemas.xmlsoap.org/soap/envelope/',
    'isee':     'http://www.hp.com/schemas/isee/5.00/entitlement',
}
reqnsmap_war = {
    'SOAP-ENV': 'http://schemas.xmlsoap.org/soap/envelope/',
    'isee':     'http://www.hp.com/isee/webservices/',
}
resnsmap = {
    'soap':     'http://schemas.xmlsoap.org/soap/envelope/',
    'isee':     'http://www.hp.com/isee/webservices/',
}

def do_request(op):
    """Called with op=register|warranty."""
    payload = etree.parse(config['ops'][op]['xml_payload'])
    for func in config['ops'][op]['hooks_req_payload']:
        func(payload)

    soapenv = etree.parse(config['ops'][op]['xml_soapenv'])
    for func in config['ops'][op]['hooks_req_soapenv']:
        func(soapenv, payload)

    # Prepare HTTP transport.
    url = config['http']['host'] + config['ops'][op]['url']
    headers = {
        'User-Agent': config['http']['user-agent'],
        'SOAPAction': config['ops'][op]['soap_action'],
        'Content-Type': config['http']['content-type'],
    }
    r = requests.post(url,
            data=etree.tostring(soapenv),
            headers=headers)

    # Handle result.
    results = []
    soapenv = etree.fromstring(r.text.encode('utf-8'))
    for func in config['ops'][op]['hooks_res_soapenv']:
        results.append(func(soapenv))
    return results

def main():
    import argparse
    import os.path

    parser = argparse.ArgumentParser(
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-a', '--authfile',
            help='JSON-formatted auth file',
            default=os.path.join(os.path.expanduser('~'), '.hpiseeauth'))
    parser.add_argument('entitlements', metavar='ENT', nargs='+',
            help='entitlement, format: serial,product,[country]')
    args = parser.parse_args()

    try:
        # Save the file path, so the auth data can be written to it
        # later in reg_getauthdata() if needed.
        config['auth']['file'] = args.authfile
        authfile = open(config['auth']['file'])
        authfilep = json.load(authfile)
        authfile.close()
        assert 'gdid' in authfilep
        assert 'regtoken' in authfilep
        assert authfilep['gdid']
        assert authfilep['regtoken']
        config['auth']['gdid'] = authfilep['gdid']
        config['auth']['regtoken'] = authfilep['regtoken']

    except IOError:
        sys.stderr.write('Registering new client.\n')
        do_request('register')

    config['entitlements'] = list(map(lambda ent:
             tuple(ent.split(',')), filter(lambda ent:
                len(ent.split(',')) == 3, args.entitlements)))
    assert len(config['entitlements'])
    assert 'gdid' in config['auth']
    assert 'regtoken' in config['auth']
    sys.stderr.write('Looking up entitlement info.\n')
    for lookup in do_request('warranty'):
        print(etree.tostring(lookup, pretty_print=True).decode('utf-8'))

if __name__ == '__main__':
    main()
