####################################################################
# 
#  Copyright (C) 2024, Northwestern University and Argonne National Laboratory
#  See COPYRIGHT notice in top-level directory.
# 
####################################################################
"""
 This example mimics the coll_perf.c from ROMIO. It creates a netcdf file and 
 writes a number of 3D integer non-record variables. The measured write bandwidth
 is reported at the end. 
 To run:
    % mpiexec -n num_process python3 collective_write.py [test_file_name] [-l len]
where len decides the size of each local array, which is len x len x len.
So, each non-record variable is of size len*len*len * nprocs * sizeof(int)
All variables are partitioned among all processes in a 3D block-block-block 
fashion.
 Example commands for MPI run and outputs from running ncmpidump on the
 netCDF file produced by this example program:
    % mpiexec -n 32 python3 collective_write.py tmp/test1.nc -l 100
    % ncmpidump tmp/test1.nc
    
    Example standard output:
    MPI hint: cb_nodes        = 2
    MPI hint: cb_buffer_size  = 16777216
    MPI hint: striping_factor = 32
    MPI hint: striping_unit   = 1048576
    Local array size 100 x 100 x 100 integers, size = 3.81 MB
    Global array size 400 x 400 x 200 integers, write size = 0.30 GB
     procs    Global array size  exec(sec)  write(MB/s)
     -------  ------------------  ---------  -----------
        32     400 x  400 x  200     6.67       45.72
"""

import sys
import os
from mpi4py import MPI
import pnetcdf
import argparse
import numpy as np
import inspect

verbose = True

NDIMS = 3
NUM_VARS = 10


def parse_help(comm):
    rank = comm.Get_rank()
    help_flag = "-h" in sys.argv or "--help" in sys.argv
    if help_flag:
        if rank == 0:
            help_text = (
                "Usage: {} [-h] | [-q] [file_name]\n"
                "       [-h] Print help\n"
                "       [-q] Quiet mode (reports when fail)\n"
                "       [-k format] file format: 1 for CDF-1, 2 for CDF-2, 5 for CDF-5\n"
                "       [-l len] size of each dimension of the local array\n"
                "       [filename] (Optional) output netCDF file name\n"
            ).format(sys.argv[0])
            print(help_text)

    return help_flag

def print_info(info_used):

    print("MPI hint: cb_nodes        =", info_used.Get("cb_nodes"))
    print("MPI hint: cb_buffer_size  =", info_used.Get("cb_buffer_size"))
    print("MPI hint: striping_factor =", info_used.Get("striping_factor"))
    print("MPI hint: striping_unit   =", info_used.Get("striping_unit"))

def pnetcdf_io(comm, filename, file_format, length):
    global verbose
    rank = comm.Get_rank()
    nprocs = comm.Get_size()

    starts = np.zeros(NDIMS, dtype=np.int32)
    counts = np.zeros(NDIMS, dtype=np.int32)
    gsizes = np.zeros(NDIMS, dtype=np.int32)
    buf = []

    psizes = MPI.Compute_dims(nprocs, NDIMS)
    starts[0] = rank % psizes[0]
    starts[1] = (rank // psizes[1]) % psizes[1]
    starts[2] = (rank // (psizes[0] * psizes[1])) % psizes[2]

    bufsize = 1
    for i in range(NDIMS):
        gsizes[i] = length * psizes[i]
        starts[i] *= length
        counts[i] = length
        bufsize *= length

    # Allocate buffer and initialize with non-zero numbers
    for i in range(NUM_VARS):
        buf.append(np.empty(bufsize, dtype=np.int32))
        for j in range(bufsize):
            buf[i][j] = rank * i + 123 + j

    comm.Barrier()
    write_timing = MPI.Wtime()

    # Create the file
    try:
        f = pnetcdf.File(filename=filename, mode = 'w', format = file_format, comm=comm, info=None)
    except OSError as e:
        print("Error at {}:{} ncmpi_create() file {} ({})".format(__file__,inspect.currentframe().f_back.f_lineno, filename, e))
        comm.Abort()
        exit(1)

    # Define dimensions
    dims = []
    for i in range(NDIMS):
        dim = f.def_dim(chr(ord('x')+i), gsizes[i])
        dims.append(dim)

    # Define variables
    vars = []
    for i in range(NUM_VARS):
        var = f.def_var("var{}".format(i), pnetcdf.NC_INT, dims)
        vars.append(var)
    # Exit the define mode
    f.enddef()

    # Get all the hints used
    info_used = f.inq_info()

    # Write one variable at a time
    for i in range(NUM_VARS):
        vars[i].put_var_all(buf[i], start = starts, count = counts)

    # Close the file
    f.close()

    write_timing = MPI.Wtime() - write_timing

    write_size = bufsize * NUM_VARS * np.dtype(np.int32).itemsize

    for i in range(NUM_VARS):
        buf[i] = None

    sum_write_size = comm.reduce(write_size, MPI.SUM, root=0)
    max_write_timing = comm.reduce(write_timing, MPI.MAX, root=0)

    if rank == 0 and verbose:
        subarray_size = (bufsize * np.dtype(np.int32).itemsize) / 1048576.0
        print_info(info_used)
        print("Local array size {} x {} x {} integers, size = {:.2f} MB".format(length, length, length, subarray_size))
        sum_write_size /= 1048576.0
        print("Global array size {} x {} x {} integers, write size = {:.2f} GB".format(gsizes[0], gsizes[1], gsizes[2], sum_write_size/1024.0))

        write_bw = sum_write_size / max_write_timing
        print(" procs    Global array size  exec(sec)  write(MB/s)")
        print("-------  ------------------  ---------  -----------")
        print(" {:4d}    {:4d} x {:4d} x {:4d} {:8.2f}  {:10.2f}\n".format(nprocs, gsizes[0], gsizes[1], gsizes[2], max_write_timing, write_bw))


def main():
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    nprocs = size
    global verbose

    if parse_help(comm):
        MPI.Finalize()
        return 1
    # Get command-line arguments
    args = None
    parser = argparse.ArgumentParser()
    parser.add_argument("dir", nargs="?", type=str, help="(Optional) output netCDF file name",\
                         default = "testfile.nc")
    parser.add_argument("-q", help="Quiet mode (reports when fail)", action="store_true")
    parser.add_argument("-k", help="File format: 1 for CDF-1, 2 for CDF-2, 5 for CDF-5")
    parser.add_argument("-l", help="Size of each dimension of the local array\n")
    args = parser.parse_args()
    file_format = None
    length = 10
    if args.q:
        verbose = False
    if args.k:
        kind_dict = {'1':None, '2':"64BIT_OFFSET", '5':"64BIT_DATA"}
        file_format = kind_dict[args.k]
    if args.l:
        if int(args.l) > 0:
            length = int(args.l)
    filename = args.dir
    if verbose and rank == 0:
        print("{}: example of collective writes".format(os.path.basename(__file__)))

    # Run pnetcdf i/o
    pnetcdf_io(comm, filename, file_format, length)

    MPI.Finalize()

if __name__ == "__main__":
    main()
