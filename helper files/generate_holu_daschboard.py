# -*- coding: utf-8 -*-
"""
Adapted script from HoLu previous to 2025, which was used to prepare the data for the power BI dashboard.
This script gathers information from various sources and combines into one datafile that can be used as input to the dashboard.
NOTE: from 2025 we (TNO) won't prepare the data like this anymore, but this script can still be used to compare this data with
the data from the samen analyseren tool.

- Sodaq : used to extract the sodaq sensor information,  including what knmi station information to use (properties.knmicode)
- KNMI  : weather data
- LML   : official measuring station
- HoLu data: actual measurements

Created on Thu Feb 16 10:42:40 2023

@author: bakrit,
@adapted_by: rogierjc (202404)
"""

from datetime import date, timedelta, datetime
import re
import pandas as pd
import numpy as np
import scipy.linalg as linalg 
import matplotlib.pyplot as plt
import os
from pathlib import Path
import traceback
import logging
from shapely.geometry import Polygon
import geopandas as gpd

logger = logging.getLogger() 
logging.basicConfig(level=logging.INFO, format='%(levelname)s : %(message)s') 

def get_sodaq_df(fd: str, year_curr: str, date_curr: str) -> pd.DataFrame:
    """
    Read the sodaq sensor data and reformat to get sodaq IDs in correct format, 
    using the provided date information to get the most recent file.

    Parameters
    ----------
    fd : str
        Path to directory containing sensor data
    year_curr : str
        Year under investigation
    date_curr : 
        End date period currently processing data for retrieving most recent file    

    Returns
    -------
    pd.DataFrame
        Dataframe containing the sodaq sensor data
    """
    sodaq_cols = ['lat', 'lon', 'name_x', 'properties.knmicode', 'properties.no2closecode', 'properties.no2stadcode', 'properties.pm10closecode', 'properties.pm25closecode']
    sodaq_df = pd.read_csv(Path(fd / year_curr / f'datastreamsLocId_{date_curr}.csv.gz'), sep=';', usecols=sodaq_cols)# Use most recent and complete idlist 
    sodaq_df['name'] = sodaq_df['name_x'].str.split('-').str[0]# TODO: dit kolom (id1) wordt volgens mij later ook weer vernoemd. kolom=id bestaat nog niet in oorspronkelijk bestand dus gebruik deze
    sodaq_df.rename({'name_x':'name_obs', 'lon':'long'}, axis=1, inplace = True)
    logger.info(f"Retrieved sodaq data")
    # print(sodaq_df.head())
    return sodaq_df

def add_municipality_codes(sodaq_df, mc_df):
    """
    Add gemeente naam using lon/lat-coordinates from TNO data.
    * Get unique sensors ([ii]) and corresponding indices ([jj]) from sodaq df
    * Get the corresponding coordinates
    * Convert to RDnew coordinate system
    * Get the municipality codes
    * Construct the municipality code column using [jj]
    """
    _, ii, jj = np.unique(sodaq_df['name_obs'], return_index = True, return_inverse = True)
    lat_lon = sodaq_df[['lat', 'long']].iloc[ii].to_numpy()
    x_rd, y_rd = GPS2RD(lat_lon[:, 0], lat_lon[:, 1])
    gemeenteCodes = get_gemeentecodes(mc_df, x_rd, y_rd)
    # gemeenteCodes_shorter = get_gemeentecodes_shorter(mc_df, x_rd, y_rd)# TODO: remove
    # print(np.array_equal(gemeenteCodes_shorter,gemeenteCodes))
    # print(len(gemeenteCodes_shorter))
    # print(gemeenteCodes_shorter)
    # for i in range(len(gemeenteCodes)):
    #     if gemeenteCodes[i] != gemeenteCodes_shorter[i]:
    #         print(f'not the same - {gemeenteCodes[i]} - {gemeenteCodes_shorter[i]}')
    #         print(x_rd[i])
    #         print(y_rd[i])
    sodaq_df['gemeenteTNO'] = gemeenteCodes[jj]
    logger.info("Added municipality codes")
    # print(sodaq_df.head())
    return sodaq_df

def get_gemeentecodes_gpd(m_df: pd.DataFrame, s_df )-> list:
    """
    Spatial join the sensor coordinates to the municipality dataframe (m_df)
    to get the gemeentecode names.

    Parameters
    ----------
    m_df   : pd.DataFrame
        Data containing column with the municipality names (gemeente) and the 
        corresponding bounding box (xMin, xMax, yMin, yMax), and polygons 
        (polygon_x, polygon_y)
    s_df   : pd.DataFrame
        DataFrame containing the sensorcoordinates 

    Returns
    -------
    pd.DataFrame
        sensor dataframe, extended with the corresponding municipality name
    """
    
    pass    

def get_gemeentecodes_shorter(S: pd.DataFrame, lon: list, lat: list) -> list:
    """
    Retrieve the municipality codes from a TNO dataset, based on the lon and lat.
    For each municipality, check if the lat-lon coordinates fall within the bbox.

    * looped over each munipality G, all lat-lon coordinates are progressed each iteration
        1) per coordinate, look if it falls within boundary of G
        2) this resuls in a list of indices of the coordinates that fall within boundary of G
        3) if any sensors (to which the coordinates belong) fall within the boundary:
            * explore if location really falls within the polygon (this reduces
            the required computation time as opposed to having to check this for 
            all polygons).  
            * if they fall indeed within the polygons 
 
    Parameters
    ----------
    S   : pd.DataFrame
        Data containing column with the municipality names (gemeente) and the 
        corresponding bounding box (xMin, xMax, yMin, yMax), and polygons 
        (polygon_x, polygon_y)
    lon : np.array
        X-coordinate (RD new)
    lat : np.array
        Y-coordinate (RD new)

    Returns
    -------
    np.array
        list of municipality codes for the provided lat-lon coordinates

    TODO: maybe make this a function that is called on the column (instead
    of looping over each row)
    """
    # print(len(lon))
    # print(lon[:, np.newaxis])
    gemeente_codes = np.array(['                        -1' for k in range(len(lon))])# TODO replace with None? # reason for this was something to do with input size
    idx_togo = ~np.zeros(len(lon), np.bool_)# TODO: check nodig op deze manier? # ja denk om bij te houden gedurende de loop 
    for k in range(len(S)):
        # check if there are coordinates that fall within the municipalities' bbox
        index_nearest = ((np.array(S['xMin'].iloc[k]) <= lon[:, np.newaxis]) & # np.newaxis to get same format as 
                        (np.array(S['xMax'].iloc[k]) >= lon[:, np.newaxis]) &
                        (np.array(S['yMin'].iloc[k]) <= lat[:, np.newaxis]) &
                        (np.array(S['yMax'].iloc[k]) >= lat[:, np.newaxis]))
        index_nearest = index_nearest.squeeze() & idx_togo
        if np.any(index_nearest):
            # print(S.iloc[k])
            # check if the sensor fall within the municipalities' polygon #TODO: check -> computation time
            test = in_polygon(np.array(S['PolygonX'].iloc[k]), np.array(S['PolygonY'].iloc[k]), lon[index_nearest], lat[index_nearest])        
            if np.any(test):
                gemeente_codes[index_nearest] = np.array(S['gemeente'].iloc[k])  
    return gemeente_codes

def get_gemeentecodes(S: pd.DataFrame, lon: list, lat: list) -> list:
    """
    Retrieve the municipality codes from a TNO dataset, based on the lon and lat.
    For each municipality, check if the lat-lon coordinates fall within the bbox.

    Loop over each munipality G, all lat-lon coordinates are progressed each iteration
        1) per coordinate, look if it falls within bbox of G
        2) this resuls in a list of indices of the coordinates that fall within boundary of G
        3) if any sensors (to which the coordinates belong) fall within the boundary:
            * explore if location really falls within the polygon (this reduces
            the required computation time as opposed to having to check this for 
            all polygons).  
            * if they fall indeed within the polygons 
 
    Parameters
    ----------
    S   : pd.DataFrame
        Data containing column with the municipality names (gemeente) and the 
        corresponding bounding box (xMin, xMax, yMin, yMax), and polygons 
        (polygon_x, polygon_y)
    lon : np.array
        X-coordinate (RD new)
    lat : np.array
        Y-coordinate (RD new)

    Returns
    -------
    np.array
        list of municipality codes for the provided lat-lon coordinates

    TODO: maybe make this a function that is called on the column (instead
    of looping over each row)
    """
    # print(len(lon))
    # print(lon[:, np.newaxis])
    gemeente_codes = np.array(['                        -1' for k in range(len(lon))])# TODO replace with None? # reason for this was something to do with input size
    idx_togo = ~np.zeros(len(lon), np.bool_)# TODO: check nodig op deze manier? # ja denk om bij te houden gedurende de loop 
    n = 0
    for k in range(len(S)):
        # check if there are coordinates that fall within the municipalities' bbox
        index_nearest = ((np.array(S['xMin'].iloc[k]) <= lon[:, np.newaxis]) & # np.newaxis to get same format as 
                        (np.array(S['xMax'].iloc[k]) >= lon[:, np.newaxis]) &
                        (np.array(S['yMin'].iloc[k]) <= lat[:, np.newaxis]) &
                        (np.array(S['yMax'].iloc[k]) >= lat[:, np.newaxis]))
        index_nearest = index_nearest.squeeze() & idx_togo
        if np.any(index_nearest):
            # check if the sensor fall within the municipalities' polygon #TODO: check -> computation time
            test = in_polygon(np.array(S['PolygonX'].iloc[k]), np.array(S['PolygonY'].iloc[k]), lon[index_nearest], lat[index_nearest])
            # print(f'number to test: {len(test)}')
            # print(f'number in polygon: {sum(test)}')      
            if np.any(test):
                gemeente_codes[index_nearest] = np.array(S['gemeente'].iloc[k])
                dum = np.flatnonzero(index_nearest)                
                dum = dum[test]
                # TODO: is it really necessary to keep track of idx_to? We are looping over the gemeentes already?
                # Apparently it is necessary because otherwise will attribute to other municipality (dat lijkt me niet goed..)
                n += len(dum)
                if n == len(lon):
                    break
                idx_togo[dum] = False    
    return gemeente_codes

def getHeaderknmi_data(fp: str, fn: str) -> (list, int): 
    # TODO: prettier to join the path already outside the function probably
    """
    Retrieve column names from data file and the count of rows with comments
    before the actual data starts.

    Parameters
    ----------
    dataPath : str
        Path to datafile under consideration
    fName    : str
        Filename from datafile under consideration

    Returns
    -------
    list
        Column names from data under consideration
    int
        Number of rows in file before actual start of the data
    """
    count = 0
    with open(os.path.join(fp, fn)) as f:
      l = f.readline()
      while ('#' in l):
            count += 1
            l = f.readline()    

    cols = list(pd.read_csv(
                    os.path.join(fp, fn),
                    skiprows = count - 1,
                    nrows = 1
                    ).keys()
                )
    return cols, count

def readknmi_data(dataPath: str, fName: str) -> pd.DataFrame:
    """

    Load the KNMI data and add a timestamp and a KNMI code.

    Parameters
    ----------
    dataPath : str
        Path to datafile under consideration
    fName    : str
        Filename from datafile under consideration

    Returns
    -------
    data
        Loaded KNMI data with knmiCode and timestamp

    """
    # print('comes here')
    cols, count = getHeaderknmi_data(dataPath, fName)
    # print('comes here')
    # print(os.path.join(dataPath, fName))

    data = pd.read_csv(os.path.join(dataPath, fName), header=None, sep=',', skiprows=count, comment='#')
    # print(data)
    ncols = np.shape(data)[1]
    data.columns = cols[0:ncols] # TODO: is er soms een andere lengte kolommen voor headerKNMI versus fname
    data.columns = data.columns.str.replace(' ','')
    data['knmiCode'] =  'knmi_06' + data['#STN'].astype('str')
    tijd = data[data.columns[1]].astype('str') + (data[data.columns[2]] - 1).astype(str).str.zfill(2)
    data['timestamp'] = pd.to_datetime(tijd, format='%Y%m%d%H', utc=True) # Aanname is dat de knmi data op de site https://www.daggegevens.knmi.nl/klimatologie/uurgegevens is UTC
    # print('Added KNMI data')
    # print(data.head())
    return data

#%% Convert RD-coordinates to lon/lat and vice-versa in the Netherlands
def GPS2RD(lat: float, lon: float) -> (float, float):
    """
    Convert WGS84 coordinates to RD-new coordinates. Writing out the conversion
    function and using it prevents having to loading the data as geospatial type 
    (and thus reducing memory and computation time).

    Parameters
    ----------
    lat : float
        latitude coordinate (WGS84)
    lon : float
        latitude coordinate (WGS84)

    Returns
    -------
    float
        X-coordinate (RD new)
    float
        Y-coordinate (RD new)

    """
    dF = 0.36 * (lat - 52.15517440)
    dL = 0.36 * (lon - 5.38720621)

    som_x = ((190094.945 * dL) + (-11832.228 * dF * dL) + (-144.221 * dF**2 * dL) + 
            (-32.391 * dL**3) + (-0.705 * dF) + (-2.340 * dF**3 * dL) + 
            (-0.608 * dF * dL**3) + (-0.008 * dL**2) + (0.148 * dF**2 * dL**3))
    som_y = ((309056.544 * dF) + (3638.893 * dL**2) + (73.077 * dF**2 ) +
            (-157.984 * dF * dL**2) + (59.788 * dF**3) + (0.433 * dL) + 
            (-6.439 * dF**2 * dL**2) + (-0.032 * dF * dL) + (0.092 * dL**4) + (-0.054 * dF * dL**4))

    x_rd = 155000 + som_x
    y_rd = 463000 + som_y
    return x_rd, y_rd
    
def RD2GPS(x_rd: float, y_rd: float) -> (float, float):
    """
    Convert RD-new coordinates to WGS84 coordinates. Writing out the conversion
    function and using it prevents having to loading the data as geospatial type 
    (and thus reducing memory and computation time).

    Parameters
    ----------
    Xrd : float
        latitude coordinate (RD new)
    Yrd : float
        latitude coordinate (RD new)

    Returns
    -------
    float
        lon-coordinate (WGS84)
    float
        lat-coordinate (WGS84)

    """
    d_xrd = (x_rd - 155000) * 10**(-5)
    d_yrd = (y_rd - 463000) * 10**(-5)

    som_n = ((3235.65389 * d_yrd) + (-32.58297 * d_xrd**2) + (-0.2475 * d_yrd**2) + (-0.84978 * d_xrd**2 * d_yrd)
             + (-0.0655 * d_yrd**3) + (-0.01709 * d_xrd**2 * d_yrd**2) + (-0.00738 * d_xrd) + (0.0053 * d_xrd**4)
             + (-0.00039 * d_xrd**2 * d_yrd**3) + (0.00033 * d_xrd**4 * d_yrd) + (-0.00012 * d_xrd * d_yrd))
    som_e = ((5260.52916 * d_xrd) + (105.94684 * d_xrd * d_yrd) + (2.45656 * d_xrd * d_yrd**2) + (-0.81885 * d_xrd**3) 
            + (0.05594 * d_xrd * d_yrd**3) + (-0.05607 * d_xrd**3 * d_yrd) + (0.01199 * d_yrd) + (-0.00256 * d_xrd**3 * d_yrd**2) 
            + (0.00128 * d_xrd * d_yrd**4) + (0.00022 * d_yrd**2) + (-0.00022 * d_xrd**2) + (0.00026 * d_xrd**5))

    lat = 52.15517 + (som_n / 3600)
    lon = 5.387206 + (som_e / 3600)
    return(lat, lon)

#%% Define in_polygon function
def in_polygon(polygon_x: float, polygon_y:float, x, y):
    """
    Given a municipality polygon (where the X and Y coordinates are split in two 
    different columns: polygon_x and polygon_y), compute per coordinates if the x and y coordinates 
    fall within that area by looping over each of the points defining the polygon. 
    For each of these points it is computed if when a haakse lijn getrokken wordt door dat punt
    het uiteindelijk een andere kant van de polygon kruist (wiskunde hoe heet dit ook alwwer TODO)  and seeing 
    X- and Y- coordinates under considerations are 
    Algorithm explanation: https://www.geeksforgeeks.org/how-to-check-if-a-given-point-lies-inside-a-polygon/

    Parameters
    ----------
    polygon_x : float
        longitude coordinates municipality polygon (RD new)
    polygon_y : float
        latitude coordinate municipality polygon  (RD new)
    x        : list
        longitude coordinates sensors locations (RD new)
    y        : list
        latitude coordinates sensors locations (RD new)

    Returns
    -------
    list
        Indicates if input points fall within polygon (True) or not (False)

    """
    n = len(polygon_x)
    inside = np.zeros(len(x), np.bool_)
    p2x = 0.0
    p2y = 0.0
    p1x = polygon_x[0]
    p1y = polygon_y[0]
    
    for i in np.arange(1, (n+1)):
        # print(f"i: {i} - % {i%n}")
        p2x = polygon_x[i % n]# TODO: why?
        p2y = polygon_y[i % n]#  TODO: why? 
        xints = []
        idx = np.nonzero((y > np.min(np.array([p1y, p2y]))) &
                         (y <= np.max(np.array([p1y, p2y]))) &
                         (x <= np.max(np.array([p1x, p2x]))))[0]# TODO: shouldn't this not check if larger than np.min([p1x, p2x])?
        # print(idx)
        if p1y != p2y:
            # print(y[idx])          
            # print(x[idx])
            xints = (y[idx]-p1y)*(p2x-p1x)/(p2y-p1y)+p1x# some kind of calculation of X-coordinates? different then x[idx], why compute?
            # print(f"not same: {xints}")  
        if p1x == p2x:# when does this happen?
            inside[idx] = ~inside[idx]
        else:                
            idxx = idx[x[idx] <= xints]
            inside[idxx] = ~inside[idxx]    

        p1x = p2x
        p1y = p2y
    return inside

#%% Fix to read lml data from luchtmeetnet api ipv HoLu api. HoLu  API is down as of March, 1st 2024
def read_lml_data(fp: str, date: str) -> pd.DataFrame:    
    """
    Parameters
    ----------
    fp  : str
        Filepath to file
    date : str
        date

    Returns
    -------
    pd.DataFrame
        LML data
    """
    jaar = date[0:4]
    maand = date[4:6]
    dag = date[6:8]
    # TODO: to pathlib instead of os.path.join
    files = np.array([os.path.join(dp, f) for dp, dn, filenames in os.walk(os.path.join(fp, jaar, maand, dag)) for f in filenames if os.path.splitext(f)[0][0]=='2'])
    idList = pd.read_csv(os.path.join(fp, jaar, maand, dag, f'idList_{date}.csv'))
    #lml_data = pd.DataFrame(columns=kolommen)
    lml_data = pd.DataFrame()
    for k in range(len(files)):    
        # print(f'read lml file: {files[k]}')
        dum = pd.read_csv(files[k])
        cols, jj = np.unique(dum['formula'], return_inverse=True)
        dum1 = pd.DataFrame(columns=cols)
        dum1['station_number'] = idList['station_number']

        for j in range(0, len(cols)):
            idx = jj==j
            dum1 = pd.merge(dum1, dum[['station_number', 'value']].loc[idx], on=['station_number'], how='left')
            dum1[cols[j]] = dum1['value'] 
            dum1 = dum1.drop(['value'], axis=1)        
        
        timeStamp = dum['timestamp_measured'].unique()
        dum1['timestamp_measured'] = np.tile(timeStamp, len(dum1))
        lml_data = pd.concat([lml_data, dum1])
    qq = idList['geometry.coordinates'].str.strip('[]').str.split(', ', expand=True).rename(columns={1:'latitude', 0:'longitude'}) 
    idList = pd.concat([idList, qq],axis=1)
    lml_data = pd.merge(lml_data, idList, on=['station_number'], how='left')
    lml_data.rename({'timestamp_measured':'timestamp', 'station_number':'device_id', 'PM10':'pm10', 'PM25':'pm25'}, axis=1, inplace=True)
    # print('Addded LML data:')
    # print(lml_data)
    return lml_data

def add_lml_data(sodaq_data_sorted, datum_str, jaar):
    # Read lml data This has been adapted since HoLu api was set dow as of March 1st 2024 (Taoufik)
    if (datum_str == '20231010')|(datum_str == '20231012'):
        lml_fname = f'holuOfficialApiData_{datum_str}_PC6.csv.gz'
        lml_data = pd.read_csv(Path(lml_dir_old / jaar / lml_fname), compression= 'gzip')
    else:
        lml_data = read_lml_data(lml_dir, datum_str)# TODO: geef lml_dir mee aan add_lml_data
        

    # Restrict to lml stations near sodaqs for PM
    lml_sodaq_pm_id = pd.DataFrame(sodaq_data_sorted['properties.pm25closecode'].unique(), columns=['device_id'])#TODO: other properties.close variables can be removed already earlier if not used?
    pm_lml_data = pd.merge(lml_data[['device_id', 'timestamp', 'pm10', 'pm25', 'longitude', 'latitude']], lml_sodaq_pm_id, on='device_id', how='inner')
    # print(f'pm lml data: {pm_lml_data}')
    # Rename lml pm data columns for clarity
    pm_lml_data.rename({'pm10':'lml_pm10', 'pm10_cal':'lml_pm10_cal', 'pm25':'lml_pm25', 'pm25_cal':'lml_pm25_cal'}, axis=1, inplace=True)# TODO: pm10_cal and pm25_cal not in data 
    # print(f'pm lml data: {pm_lml_data}')
    
    # standarise time on both sides:
    sodaq_data_sorted['phenomenonTime'] = pd.to_datetime(sodaq_data_sorted['phenomenonTime']) 
    pm_lml_data['timestamp'] = pd.to_datetime(pm_lml_data['timestamp'])
    
    # Merge lml pm data
    sodaq_data_sorted = pd.merge(sodaq_data_sorted, pm_lml_data, left_on=['phenomenonTime', 'properties.pm25closecode'], right_on=['timestamp', 'device_id'], how='left')
    # sodaq_data_sorted['phenomenonTime'] = pd.to_datetime(sodaq_data_sorted['phenomenonTime']) # convert from Zulu format to utc

    return sodaq_data_sorted

def get_cols_old_dashboard():
    """
    The original dashboard file is read, with the only goal of retrieving
    the column names used. This ensures that the same format is used in the 
    current work.
    """
    db_old = pd.read_csv(old_dashboard_fp)
    cols_old_db = db_old.columns.to_list()
    return cols_old_db


def add_holu_data(final_df, sodaq_df):    
    """
    Retrieve the HoLu data and add to the final df
    """
    numDays = (datum_to - datum_from).days
    cols = final_df.columns
    errors = []

    # components = sodaq_df['component'].unique()
    for k in range(numDays):
        try:
            logger.info(f"Processing day {k+1}")
            datum = datum_from + timedelta(days=k)
            datum_str = datum.strftime('%Y%m%d')
            jaar = datum_str[0:4]

            # Load data
            # Load samenmeten data
            try:
                samenmeten_data = pd.read_csv(Path(sodaq_dir / jaar / f'kogerveldData_{datum_str}.csv'))
            except FileNotFoundError:
                errors.append({"date_with_errors": datum_str, "error_source": "samenmeten_data (FileNotFoundError)"})
                continue
            except pd.errors.EmptyDataError:
                errors.append({"date_with_errors": datum_str, "error_source": "samenmeten_data (EmptyDataError)"})
                continue

            try:
                knmi_data = readknmi_data(Path(knmi_dir / jaar), f'knmiWindData_{datum_str}.csv')
            except FileNotFoundError:
                errors.append({"date_with_errors": datum_str, "error_source": "knmi_data (FileNotFoundError)"})
                continue
            except pd.errors.EmptyDataError:
                errors.append({"date_with_errors": datum_str, "error_source": "knmi_data (EmptyDataError)"})
                continue
            # print(f'KNMI Data: {knmi_data}')

            # Restrict to Sodaq sensors and components of interest
            # TODO: check: seems that not all sodaq sensors are in the samenmeten data?
            sodaq_data = pd.merge(samenmeten_data, sodaq_df, left_on='name', right_on='name_obs', how='inner')
            # print(sodaq_data)
            sodaq_data['name'] = sodaq_data['name_obs'].str.split('-').str[0]

            sodaq_data_sorted = pd.pivot_table(sodaq_data, values='result', index=['name', 'phenomenonTime'], columns='component').reset_index()
            # sodaq_data_sorted = sodaq_data.set_index([x for x in sodaq_data.columns if x!='result'])['result'].unstack('component')
            # Merge data with sodaq_ids
            _, ii = np.unique(sodaq_df['name'], return_index = True)
            sodaq_data_sorted = pd.merge(sodaq_data_sorted, sodaq_df.iloc[ii], on='name', how='left')      
            
            # sodaq_data_sorted = add_lml_data(sodaq_data_sorted, datum_str, jaar)
            try:
                sodaq_data_sorted = add_lml_data(sodaq_data_sorted, datum_str, jaar)
            except FileNotFoundError as e:
                errors.append({
                    "date_with_errors": datum_str,
                    "error_source": f"add_lml_data (FileNotFoundError): {str(e)}"
                })
                continue
            except Exception as e:
                errors.append({
                    "date_with_errors": datum_str,
                    "error_source": f"add_lml_data (UnexpectedError): {type(e).__name__}: {str(e)}"
                })
                continue
            
            # check if sodaq data contains pm25 values for this date
            if 'pm25' not in sodaq_data_sorted.columns:
                errors.append({
                    'date_with_errors': datum_str,
                    'error_source': f"lml data did not contain pm25 value for this date"
                })
                continue 

            sodaq_data_sorted = pd.merge(sodaq_data_sorted, knmi_data[['timestamp', 'knmiCode', 'DD', 'FH', 'T', 'RH', 'U']], left_on=['phenomenonTime', 'properties.knmicode'], right_on=['timestamp', 'knmiCode'], how='left')
            sodaq_data_sorted['id'] = sodaq_data_sorted['name'] # TODO: ugly fix. But name col = id col (both name and id column is kept to folow same column as old dashboard)
            sodaq_data_sorted = sodaq_data_sorted[cols]
            final_df = pd.concat([final_df, sodaq_data_sorted])
            # print(f'final df: \n{final_df}')
        except Exception as exc:
            print(f'something went wrong at {datum_str}')
            print(f"Error type: {type(exc)}")
            print(exc.args)
            # print(traceback.format_exc())
    print(errors)
    return final_df, errors

def harmonise_colnames(df):
    # print(df)
    df.rename({'phenomenonTime':'time', 'pm25':'measurement',
            'properties.knmicode':'knmicode', 'properties.pm25closecode':'pm25closecode',
            'temp':'temperatuur', 'rh':'luchtvochtigheid', 'DD':'windrichting',
            'lml_pm25':'luchtmeetnet', 'longitude':'lml_long', 'latitude':'lml_lat',
            'gemeenteTNO':'gemeente', 
            # below columns are for sensor data cleaning pipeline : DD is also from KNMI but don't want to miss with current dashboard column names
            'FH': 'wind_snelheid_knmi', 'T': 'temp_knmi', 'RH': 'neerslag_uur_knmi', 'U': 'luchtvochtigheid_knmi'
            },axis=1,inplace = True)
    df['verschil_luchtmeetnet'] = df['measurement'] - df['luchtmeetnet']
    df['luchtmeetnetstation'] = df['pm25closecode']
    df['dag'] = df['time'].dt.day
    df['datum'] = df['time'].dt.date.astype('str')
    df['jaar'] = df['time'].dt.year
    df['maand'] = df['time'].dt.month
    df['tijd'] = df['time'].dt.time
    df['uur'] = df['time'].dt.hour
    df['weekdag'] = df['time'].dt.weekday
    df['weeknummer'] = df['time'].dt.isocalendar().week
    df['luchtvochtigheid'] = np.floor(df['luchtvochtigheid'])
    df['temperatuur'] = np.floor(10*df['temperatuur'])/10
    return df

def add_lml_distance(df):
    _, ii, jj = np.unique(df['name'], return_index=True, return_inverse = True)
    lat_lon = df[['lat','long']].iloc[ii].to_numpy()
    x_rd, y_rd = GPS2RD(lat_lon[:, 0], lat_lon[:, 1]) # coordinates of the unique sensors
    lat_lon = df[['lml_lat','lml_long']].iloc[ii].to_numpy().astype('float') # coordinates coupled lml station
    lml_x_rd, lml_y_rd = GPS2RD(lat_lon[:, 0],lat_lon[:, 1])
    df['distance_to_lml [m]'] = np.floor(np.sqrt((lml_x_rd[jj] - x_rd[jj])**2 + (lml_y_rd[jj] - y_rd[jj])**2))
    return df


def parse_knmi_station_coords(filepath: str) -> dict:
    """Parse KNMI station coordinates from the header of a knmiWindData file.

    Lines of the form '# 225         4.555       52.463      4.40        IJmuiden'
    are parsed into a dict mapping knmiCode -> (lon, lat).

    Returns
    -------
    dict
        {knmiCode: (lon_wgs84, lat_wgs84), ...}
    """
    stations = {}
    pattern = re.compile(
        r'^#\s+(\d{3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(.+)'
    )
    with open(filepath, 'r') as f:
        for line in f:
            if not line.startswith('#') and not line.startswith('"#'):
                break
            m = pattern.match(line)
            if m:
                stn = m.group(1)
                lon = float(m.group(2))
                lat = float(m.group(3))
                knmi_code = f'knmi_06{stn}'
                stations[knmi_code] = (lon, lat)
    return stations


def add_knmi_distance(df: pd.DataFrame) -> pd.DataFrame:
    """Compute distance (in km) between each sensor and its assigned KNMI station.

    Uses the first available knmiWindData file to parse KNMI station coordinates.
    Distance is computed in RD New (EPSG:28992) for accuracy.
    """
    # Find a KNMI file to parse station coordinates from
    sample_file = None
    for year_dir in sorted(knmi_dir.iterdir()):
        if year_dir.is_dir():
            for f in sorted(year_dir.iterdir()):
                if f.name.startswith('knmiWindData'):
                    sample_file = str(f)
                    break
            if sample_file:
                break

    if sample_file is None:
        logger.warning("No knmiWindData file found — cannot compute KNMI station distance")
        df['distance_to_knmi_km'] = np.nan
        return df

    print(f"Parsing KNMI station coordinates from {sample_file}")
    knmi_stations = parse_knmi_station_coords(sample_file)
    logger.info(f"Parsed {len(knmi_stations)} KNMI station coordinates")

    # Get unique sensors and their assigned KNMI codes
    _, ii, jj = np.unique(df['name'], return_index=True, return_inverse=True)
    sensor_lat_lon = df[['lat', 'long']].iloc[ii].to_numpy()
    sensor_x_rd, sensor_y_rd = GPS2RD(sensor_lat_lon[:, 0], sensor_lat_lon[:, 1])

    knmi_codes = df['knmicode'].iloc[ii].values

    # Compute distance per unique sensor
    distances_km = np.full(len(ii), np.nan)
    for k, code in enumerate(knmi_codes):
        if code in knmi_stations:
            knmi_lon, knmi_lat = knmi_stations[code]
            knmi_x, knmi_y = GPS2RD(np.array([knmi_lat]), np.array([knmi_lon]))
            distances_km[k] = np.sqrt(
                (knmi_x[0] - sensor_x_rd[k])**2 + (knmi_y[0] - sensor_y_rd[k])**2
            ) / 1000.0  # metres to km

    df['distance_to_knmi_km'] = np.round(distances_km[jj], 2)

    if df['distance_to_knmi_km'].isna().any():
        nan_sensors = df.loc[df['distance_to_knmi_km'].isna(), ['name', 'knmicode']].drop_duplicates()
        raise ValueError(
            f"{len(nan_sensors)} sensor(s) have NaN distance to KNMI station. "
            f"Affected sensors:\n{nan_sensors.to_string(index=False)}"
        )

    logger.info(f"KNMI distances: min={np.nanmin(distances_km):.1f} km, "
                f"max={np.nanmax(distances_km):.1f} km, "
                f"median={np.nanmedian(distances_km):.1f} km")
    return df

#%% Set some paths and varbs
# data_dir = Path('\\\\tsn.tno.nl\data\sv\sv-052045\Jorane')
data_dir = Path('\\\\tsn.tno.nl\\data\\sv\\sv-052045\\Kluis\\Projects\\2019\\kip_MSI\\')
lml_dir_old = Path('\\\\app-reken06\\Data-Performance\\ProjectData\\HoLu\\dailyBasedData\\' )# LML data
lml_dir = Path('\\\\app-reken12\\L\\UserData\\bakrit\\RIVMfeedApiBatch\\') # LML data retrieved from different  location
## TODO: uncomment below two lines and remove intialisation after those lines
sodaq_dir = Path(data_dir / 'DataKogerveld')# contains the sensor data
# knmi_dir = Path(data_dir / 'knmiUurgegevens')# contains the KNMI data
# sodaq_dir = Path('\\\\tsn.tno.nl\\data\\sv\\sv-052045\\Kluis\\Projects\\2019\\kip_MSI\\DataKogerveld\\')
knmi_dir = Path('\\\\tsn.tno.nl\\data\\sv\\sv-052045\\Ralph\\projects\\HollandseLuchten\\data\\knmiUurgegevens')# contains the KNMI data
old_dashboard_fp = Path('\\\\tsn.tno.nl\\data\\sv\\sv-052045\\Kluis\\Projects\\2022\\Ralph\\HollandseLuchten\\data\\sodaq_luchtmeetnet.csv') # contains old data from the old dashboard (Gerda). Used to have output in identical format.
municipality_codes_fp = Path('\\\\tsn.tno.nl\\data\\sv\\sv-052045\\Kluis\\Projects\\2022\\Ralph\\HollandseLuchten\\pythonCode\\GemeentenCodes2022') #geo data from gemeenten and matching TNO code
output_dir = Path(Path(__file__).parents[2] / 'data\\tmp\\')

#%% Set time range
datum_from = date(2026, 5, 1)
datum_to = date(2026, 5, 8)# (2024, 3, 19))
datum_str_from = datum_from.strftime('%Y%m%d')
datum_str_to = datum_to.strftime('%Y%m%d')
jaar = datum_str_to[0:4]

# read old dashboard file to extract columnnames in order to keep same format
db_old = pd.read_csv(old_dashboard_fp)
cols_old_db = db_old.columns.to_list()
del db_old

output_period_dir = Path(output_dir / 'sensor_data' / f'{datum_str_from}_{datum_str_to}')
if not output_period_dir.exists():
    output_period_dir.mkdir(exist_ok=True)


def main():
    """
    This function, the main function, shows the flow of the code. 
    """
    # Load the sensor data
    sodaq_df = get_sodaq_df(sodaq_dir, jaar, datum_str_to)
    # print(sodaq_df)
    # The municipality code should already be added here, since we need
    # to filter explicitly on municipality==Hilversum (otherwise, adding the 
    # municipality code once after filtering would have been sufficient).
    # NOTE:  filtering on Hilversum should be done, otherwise the filtering 
    # on splitted names won't work (different layouts of sensor names)   
    municipality_codes_df = pd.read_pickle(municipality_codes_fp)
    sodaq_df = add_municipality_codes(sodaq_df, municipality_codes_df)

    # Restrict data to sensors of interest for the HoLu project
    # (id_code > 221 OR municipality_code == Hilversum)
    sodaq_df = sodaq_df.loc[((sodaq_df['name'].str.startswith('HLL_hl_device')) | \
                              (sodaq_df['gemeenteTNO'].str.lower()=='hilversum'))]
    sodaq_df['id'] = sodaq_df['name_obs'].str.split('-').str[0].str.split('_').str[-1].astype(float)
    sodaq_df = sodaq_df.loc[(((sodaq_df['name'].str.startswith('HLL_hl_device'))
                              & (sodaq_df['id'] >= 221))
                              | (sodaq_df['gemeenteTNO'].str.lower()=='hilversum'))]
    sodaq_df['properties.knmicode'] = sodaq_df['properties.knmicode'].str.replace('knmi_06257', 'knmi_06225') # Dirty trick overgenomen van Gerda. Moet tzt anders!! # NOTE: eerder op een slimmere manier de dichtsbijzijnde station vinden
    
    # Intersect sodaq_df with sodaq stations and enrich sodaq df with station info
    sodaq_df['component'] = sodaq_df['name_obs'].str.split('-').str[-1].to_numpy()
    sodaq_df = add_municipality_codes(sodaq_df, municipality_codes_df)

    #  Initialise final sodaq dataframe.
    columns_to_keep = ['phenomenonTime', 'name', 'rh', 'temp', 'pm25_kal', 'pm25', 
                    'properties.knmicode', 'properties.no2closecode',
                    'properties.pm10closecode', 'properties.pm25closecode', 
                    'long', 'lat', 'id', 'gemeenteTNO', 'lml_pm10', 'lml_pm25',
                    'longitude', 'latitude', 'DD',
                    # Below are not for the dashboard but might be of interest in the methodology
                    'FH',# average windspeed past hour 
                    'T', # might be useful as comparison to the temperature of the sensors  in the sensor cleaning process
                    'RH', # Hourly precipitation amount (in 0.1 mm) (-1 for <0.05 mm)
                    'U'# Relative atmospheric humidity (in percents) at 1.50 m at the time of observation
                    ]
    
    sodaq_final_df = pd.DataFrame(columns=columns_to_keep)

    # Add the HoLu data for the whole period (datum_from - datum_to)
    sodaq_final_df, dates_with_errors = add_holu_data(sodaq_final_df, sodaq_df)
    dates_with_errors_df = pd.DataFrame(dates_with_errors)
    dates_with_errors_df.to_csv((output_dir / 'dates_with_errors.csv'), index=False)

    sodaq_final_df = harmonise_colnames(sodaq_final_df)   
    sodaq_final_df.sort_values(['name', 'time'], inplace=True)

    # Add distance to nearest lml_station
    sodaq_final_df = add_lml_distance(sodaq_final_df)

    # Add distance to assigned KNMI station
    sodaq_final_df = add_knmi_distance(sodaq_final_df)

    cols_ext = np.append(np.array(cols_old_db), np.setdiff1d(np.array(sodaq_final_df.keys()), np.array(cols_old_db)))
    sodaq_final_df = sodaq_final_df[cols_ext]
    sodaq_final_df.to_csv((output_period_dir / f'holu_data_ext_{datum_str_from}_{datum_str_to}.csv'), index=False)
    sodaq_final_df[cols_old_db].to_csv((output_period_dir / f'holu_data_{datum_str_from}_{datum_str_to}.csv'), index=False)
    
if __name__ == '__main__':
    main()