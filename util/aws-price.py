#!/usr/bin/env python

import argparse
import json
import logging
import re
import os
import sys

from decimal import Decimal

from pyramid.paster import (
    get_appsettings,
    setup_logging,
    )
from pyramid.scripts.common import parse_vars

from budget.models import DBSession, Base, AwsPrice, AwsProduct
from budget.util.fileloader import load_json

from sqlalchemy import engine_from_config

config_uri = os.path.dirname(os.path.abspath(__file__)) + '/../production.ini'
ec2_region_map = os.path.dirname(os.path.abspath(__file__)) + '/../data/aws_pricing/ec2_region_map.json'

def lookup_price(options):
    ''' Digs through a massive nest of json data to extract the on demand
        pricing for AWS instances.

        See also:
        https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html

        Params:

        instance_type: any valid AWS instance size. (e.g. 'm4.xlarge')
        region: an AWS region endpoint name. (e.g. 'us-east-1')
        tenancy: 'Shared' or 'Dedicated'
        pricing: 'OnDemand' or 'Reserved'
        lease_contract_length: '1yr' or '3yr'
        purchase_option: 'No Upfront' or 'Partial Upfront' or 'Full Upfront'

        Returns:

        dict: key   - 'Hrs' or 'Quantity'
              value - Decimal
    '''

    region_name = region_lookup(options.region)
    products = DBSession.query(
                    AwsPrice.price_dimensions,
                    AwsPrice.term_attributes
              ).filter(
                    AwsProduct.instance_type == options.instance_type,
                    AwsProduct.location == region_name,
                    AwsProduct.tenancy == options.tenancy,
                    AwsProduct.operating_system == options.operating_system,
                    AwsPrice.sku == AwsProduct.sku
              ).all()

    costs = []
    for p in products:
        price_dimensions = json.loads(p[0])
        term_attributes = json.loads(p[1])

        if options.pricing == 'OnDemand':
            rgx = re.compile(r'On Demand %s %s' % (options.operating_system,
                    options.instance_type))
            costs.append(_find_cost(rgx, price_dimensions))
        elif options.pricing == 'Reserved':
            # On-Demand has no term_attributes
            if term_attributes == {}:
                continue

            for k,v in price_dimensions.items():
                term_attributes.update({v['description']:v['pricePerUnit']})

            costs.append(term_attributes)
    return costs


def region_lookup(region):
    for x in load_json(ec2_region_map):
        if x['region'] == region:
            return x['region_name']
    return None

def _find_cost(regex, price_dimensions):
    ''' the price dimension portion of the AWS Pricing data is deeply nested.
        this method extracts the elements of that data we care about based on a
        regex search of the Description field.
    '''
    costs = {}
    matched = False
    for dim in price_dimensions:
        rate = price_dimensions[dim]['pricePerUnit']['USD']
        units = price_dimensions[dim]['unit']
        costs[units] = Decimal(rate)

        desc = price_dimensions[dim]['description']
        if regex.search(desc):
            matched = True
    if matched:
        return costs
    return {}

def parse_args(args):
    parser = argparse.ArgumentParser(description='Look up AWS Pricing')
    parser.add_argument('--instance-type', dest='instance_type', type=str,
             action='store', default='m4.xlarge', help='An Instance Type (e.g. m1.small)')
    parser.add_argument('--region', dest='region', action='store', type=str,
             default='us-east-1', help='AWS Region (e.g. us-east-1')
    parser.add_argument('--pricing', dest='pricing', action='store', type=str,
             default='OnDemand', help='OnDemand or Reserved')
    parser.add_argument('--tenancy', dest='tenancy', action='store', type=str,
             default='Shared', help='Shared or Dedicated')
    parser.add_argument('--operating-system', dest='operating_system', action='store', type=str,
             default='Linux', help='Linux or Windows')
    return parser.parse_args(args)

def main(args=sys.argv):
    options = parse_args(args[1:])
    setup_logging(config_uri)
    global log
    log = logging.getLogger(__name__)

    settings = get_appsettings(config_uri)
    engine = engine_from_config(settings, 'sqlalchemy.')
    DBSession.configure(bind=engine)

    price = lookup_price(options)
    if options.pricing == 'OnDemand':
        for p in price:
            for k,v in p.items():
                print '%s %s in %s: %s per %s' % (options.pricing, options.instance_type, options.region, v,k)
    elif options.pricing == 'Reserved':
        for p in sorted(price, key=lambda x: x['PurchaseOption']):
            print '%s %s' % (p.pop('LeaseContractLength'), p.pop('PurchaseOption'))
            for k,v in p.items():
                print '\t%s - %s %s' % (k, v.items()[0][1], v.items()[0][0])


if '__main__' in __name__:
    try:
        main()
    except KeyboardInterrupt:
        print "Ctrl-C detected. Exiting."
        sys.exit()
