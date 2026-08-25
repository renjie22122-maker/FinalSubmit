package org.twak.readTrace;

import java.io.File;
import java.util.Collections;

import javax.vecmath.Matrix4d;
import javax.vecmath.Vector3d;

/** Command-line bridge used by the Windows data preparation project. */
public class MiniTransformCLI {

	private static void usage() {
		System.err.println( "usage: MiniTransformCLI <input.obj> <output-dir> "
				+ "[Y_UP|X_UP|Z_UP] [offset-x offset-y offset-z]" );
	}

	public static void main( String[] args ) {
		if ( args.length != 2 && args.length != 3 && args.length != 6 ) {
			usage();
			System.exit( 2 );
		}

		File input = new File( args[ 0 ] ).getAbsoluteFile();
		File output = new File( args[ 1 ] ).getAbsoluteFile();
		if ( !input.isFile() )
			throw new IllegalArgumentException( "input OBJ not found: " + input );
		if ( output.equals( input ) || output.equals( input.getParentFile() ) )
			throw new IllegalArgumentException( "output must be a dedicated directory" );

		MiniTransform.Orientation orientation = args.length >= 3
				? MiniTransform.Orientation.valueOf( args[ 2 ].toUpperCase() )
				: MiniTransform.Orientation.Y_UP;
		Vector3d intentionalOffset = new Vector3d();
		if ( args.length == 6 )
			intentionalOffset.set(
					Double.parseDouble( args[ 3 ] ),
					Double.parseDouble( args[ 4 ] ),
					Double.parseDouble( args[ 5 ] ) );

		Matrix4d transform = new Matrix4d( orientation.m );
		MiniTransform.convertToMini(
				Collections.singletonList( input ), output, transform, true, intentionalOffset );
		System.out.println( "mini-mesh written to " + output );
	}
}
