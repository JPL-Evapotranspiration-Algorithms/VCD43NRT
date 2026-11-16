from os.path import join, abspath, dirname
import numpy as np
from numpy import where, isnan, logical_not, nanmean, nan, logical_or, loadtxt, digitize, arange


def statistical_radiative_transport(SZA, AOT):
    filename = join(abspath(dirname(__file__)), 'statistical_radiative_transport.txt')

    data = loadtxt(filename)

    sza_index = digitize(SZA, arange(0, 90, 1)[:-1])
    aot_index = digitize(AOT, arange(0, 1, 0.02)[:-1])

    SRT = data[sza_index, aot_index]

    return SRT


def bidirectional_reflectance(white_sky_albedo, black_sky_albedo, SZA, AOT):
    # constrain aerosol optical thickness
    AOT = np.clip(AOT, 0.1, 0.97)
    if np.all(np.isnan(AOT)):
        AOT = np.full_like(AOT, 0.1, dtype=float)
    else:
        AOT_mean = np.nanmean(AOT)
        AOT = np.where(np.isnan(AOT), AOT_mean, AOT)

    # constrain SZA
    invalid_sza = logical_or(SZA <= 0, SZA > 90)
    valid_sza = where(logical_not(invalid_sza), SZA, nan)
    if np.all(np.isnan(valid_sza)):
        SZA = np.full_like(SZA, 45.0, dtype=float)
    else:
        SZA = where(invalid_sza, nanmean(valid_sza), SZA)
        SZA = where(isnan(SZA), 45.0, SZA)

    # lookup statistical radiative transport
    SRT = statistical_radiative_transport(SZA, AOT)

    # cloud filter albedo
    blue_sky_albedo = white_sky_albedo * SRT + black_sky_albedo * (1 - SRT)

    return blue_sky_albedo
